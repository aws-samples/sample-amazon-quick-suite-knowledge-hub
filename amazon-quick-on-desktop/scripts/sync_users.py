#!/usr/bin/env python3
"""Syncs users from IAM Identity Center or local Amazon Quick users into a Cognito User Pool.

Auto-discovers the Cognito Pool ID from the CloudFormation stack. Shows a
plan and prompts for confirmation before creating users. Each created user
receives a Cognito invitation email with a one-time temporary password and
sets their own password on first sign-in.

Usage:
    python3 sync_users.py --source idc    # from IAM Identity Center
    python3 sync_users.py --source local  # from local Amazon Quick users
"""

import argparse
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError


class UserSource(StrEnum):
    IDC = "idc"
    LOCAL = "local"


class StackOutput(StrEnum):
    POOL_ID = "PoolId"


class StackName(StrEnum):
    COGNITO_PROXY = "QuickDesktopCognitoProxyStack"


class SyncError(Exception):
    pass


class StackNotFoundError(SyncError):
    def __init__(self, stack_name: str) -> None:
        super().__init__(f"Stack {stack_name} not found. Deploy first.")


class IdcNotFoundError(SyncError):
    def __init__(self) -> None:
        super().__init__("No IAM Identity Center instance found.")


@dataclass(frozen=True)
class SyncUser:
    username: str
    email: str


class SyncStatus(StrEnum):
    CREATED = "CREATED"
    EXISTS = "EXISTS"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class UserSyncResult:
    user: SyncUser
    status: SyncStatus


@dataclass(frozen=True)
class StackOutputResponse:
    pool_id: str


@dataclass(frozen=True)
class SyncRequest:
    source: UserSource


@dataclass(frozen=True)
class SyncResponse:
    created: int
    resent: int
    skipped: int
    failed: int


class StackOutputResolver:
    def __init__(self, cfn_client: BaseClient) -> None:
        self._cfn = cfn_client

    def resolve(self) -> StackOutputResponse:
        resp = self._cfn.describe_stacks(StackName=StackName.COGNITO_PROXY)
        stacks = resp.get("Stacks", [])
        if not stacks:
            raise StackNotFoundError(StackName.COGNITO_PROXY)
        outputs = {
            o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])
        }
        if StackOutput.POOL_ID not in outputs:
            raise StackNotFoundError(StackName.COGNITO_PROXY)
        return StackOutputResponse(pool_id=outputs[StackOutput.POOL_ID])


class IdcUserLister:
    def __init__(
        self, sso_client: BaseClient, identitystore_client: BaseClient
    ) -> None:
        self._sso = sso_client
        self._ids = identitystore_client

    def list_users(self) -> Iterator[SyncUser]:
        resp = self._sso.list_instances()
        instances = resp.get("Instances", [])
        if not instances:
            raise IdcNotFoundError()

        identity_store_id = instances[0]["IdentityStoreId"]
        paginator = self._ids.get_paginator("list_users")
        for page in paginator.paginate(IdentityStoreId=identity_store_id):
            for user in page["Users"]:
                emails = [e["Value"] for e in user.get("Emails", []) if e.get("Value")]
                username = user.get("UserName", "")
                yield SyncUser(
                    username=username,
                    email=emails[0] if emails else username,
                )


class LocalUserLister:
    """Lists local Amazon Quick users."""

    def __init__(self, qs_client: BaseClient, account_id: str) -> None:
        self._qs = qs_client
        self._account_id = account_id

    def list_users(self) -> Iterator[SyncUser]:
        paginator = self._qs.get_paginator("list_users")
        for page in paginator.paginate(
            AwsAccountId=self._account_id, Namespace="default"
        ):
            for user in page["UserList"]:
                if not user.get("Active", True):
                    continue
                yield SyncUser(
                    username=user.get("UserName", ""),
                    email=user.get("Email", ""),
                )


class CognitoUserSyncer:
    """Creates users and lets Cognito send the invitation email."""

    def __init__(self, cognito_client: BaseClient, pool_id: str) -> None:
        self._cognito = cognito_client
        self._pool_id = pool_id

    def plan(self, users: Iterator[SyncUser]) -> list[UserSyncResult]:
        results = []
        for user in users:
            if not user.username:
                results.append(UserSyncResult(user=user, status=SyncStatus.SKIPPED))
            elif self._user_exists(user.username):
                results.append(UserSyncResult(user=user, status=SyncStatus.EXISTS))
            else:
                results.append(UserSyncResult(user=user, status=SyncStatus.CREATED))
        return results

    def create(self, user: SyncUser) -> None:
        """Creates a user. Cognito emails an invitation with a temporary password."""
        attrs = [{"Name": "email_verified", "Value": "true"}]
        if user.email:
            attrs.append({"Name": "email", "Value": user.email})

        self._cognito.admin_create_user(
            UserPoolId=self._pool_id,
            Username=user.username,
            UserAttributes=attrs,
            DesiredDeliveryMediums=["EMAIL"],
        )

    def resend_invite(self, user: SyncUser) -> None:
        """Resends the invitation email to a user who has not yet signed in."""
        self._cognito.admin_create_user(
            UserPoolId=self._pool_id,
            Username=user.username,
            MessageAction="RESEND",
            DesiredDeliveryMediums=["EMAIL"],
        )

    def _user_exists(self, username: str) -> bool:
        try:
            self._cognito.admin_get_user(UserPoolId=self._pool_id, Username=username)
            return True
        except self._cognito.exceptions.UserNotFoundException:
            return False


class UserSyncOrchestrator:
    def __init__(
        self,
        stack_resolver: StackOutputResolver,
        syncer_factory: Callable[[str], CognitoUserSyncer],
        idc_lister_factory: Callable[[], IdcUserLister],
        local_lister_factory: Callable[[], LocalUserLister],
    ) -> None:
        self._stack_resolver = stack_resolver
        self._syncer_factory = syncer_factory
        self._idc_lister_factory = idc_lister_factory
        self._local_lister_factory = local_lister_factory

    def _confirm(self, prompt: str) -> bool:
        while True:
            response = input(f"{prompt} [y/n] ").strip().lower()
            match response:
                case "y":
                    return True
                case "n":
                    return False
                case _:
                    print("    Please enter y or n.")

    def run(self, request: SyncRequest) -> SyncResponse:
        stack = self._stack_resolver.resolve()

        match request.source:
            case UserSource.IDC:
                print("Source: IAM Identity Center")
                users = self._idc_lister_factory().list_users()
            case UserSource.LOCAL:
                print("Source: Local Amazon Quick users")
                users = self._local_lister_factory().list_users()

        print(f"Pool:    {stack.pool_id}\n")

        syncer = self._syncer_factory(stack.pool_id)
        plan = syncer.plan(users)

        created = resent = skipped = failed = 0
        for result in plan:
            match result.status:
                case SyncStatus.SKIPPED:
                    skipped += 1
                case SyncStatus.CREATED:
                    if not self._confirm(
                        f"  Invite {result.user.username} ({result.user.email})?"
                    ):
                        skipped += 1
                        continue
                    try:
                        syncer.create(result.user)
                        print(f"    INVITED: {result.user.username}")
                        created += 1
                    except (BotoCoreError, ClientError) as e:
                        print(f"    FAILED:  {result.user.username}: {e}")
                        failed += 1
                case SyncStatus.EXISTS:
                    if not self._confirm(
                        f"  {result.user.username} exists. Resend invitation?"
                    ):
                        skipped += 1
                        continue
                    try:
                        syncer.resend_invite(result.user)
                        print(f"    RESENT: {result.user.username}")
                        resent += 1
                    except (BotoCoreError, ClientError) as e:
                        print(f"    FAILED:  {result.user.username}: {e}")
                        failed += 1

        print(
            f"\nDone. Invited: {created}, Resent: {resent}, Skipped: {skipped}, Failed: {failed}"
        )
        return SyncResponse(
            created=created, resent=resent, skipped=skipped, failed=failed
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source", type=UserSource, default=UserSource.IDC, choices=list(UserSource)
    )
    args = parser.parse_args()

    session = boto3.Session()
    cognito = session.client("cognito-idp")
    sts = session.client("sts")

    orchestrator = UserSyncOrchestrator(
        stack_resolver=StackOutputResolver(session.client("cloudformation")),
        syncer_factory=lambda pool_id: CognitoUserSyncer(cognito, pool_id),
        idc_lister_factory=lambda: IdcUserLister(
            session.client("sso-admin"), session.client("identitystore")
        ),
        local_lister_factory=lambda: LocalUserLister(
            session.client("quicksight"),
            sts.get_caller_identity()["Account"],
        ),
    )

    try:
        orchestrator.run(SyncRequest(source=args.source))
    except (SyncError, BotoCoreError, ClientError) as e:
        sys.exit(f"ERROR: {e}")
    except Exception as e:
        sys.exit(f"UNEXPECTED ERROR: {e}")
