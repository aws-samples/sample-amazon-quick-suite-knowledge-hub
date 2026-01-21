"""
Quick Suite Setup Lambda Handler

This Lambda function handles the setup of Amazon Quick Suite with IAM Identity Center.
It can be invoked as a CloudFormation Custom Resource or directly via Lambda invocation.
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class QuickSuiteSetup:
    """Handles Quick Suite setup operations."""

    def __init__(self, region: str = None):
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.quicksight = boto3.client("quicksight", region_name=self.region)
        self.sso_admin = boto3.client("sso-admin", region_name=self.region)
        self.identity_store = boto3.client("identitystore", region_name=self.region)
        self.iam = boto3.client("iam", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)

        # Get account ID
        self.account_id = self.sts.get_caller_identity()["Account"]

    def create_quicksight_subscription(
        self, account_name: str, admin_email: str
    ) -> dict:
        """Create QuickSight account subscription."""
        logger.info(f"Creating QuickSight subscription: {account_name}")

        try:
            # Check if subscription already exists
            try:
                existing = self.quicksight.describe_account_subscription(
                    AwsAccountId=self.account_id
                )
                logger.info(f"QuickSight subscription already exists: {existing}")
                return {"status": "EXISTS", "subscription": existing}
            except self.quicksight.exceptions.ResourceNotFoundException:
                pass

            # Create new subscription
            response = self.quicksight.create_account_subscription(
                AwsAccountId=self.account_id,
                AccountName=account_name,
                NotificationEmail=admin_email,
                AuthenticationMethod="IAM_IDENTITY_CENTER",
                Edition="ENTERPRISE",
            )

            logger.info(f"QuickSight subscription created: {response}")
            return {"status": "CREATED", "subscription": response}

        except ClientError as e:
            logger.error(f"Error creating QuickSight subscription: {e}")
            raise

    def create_quicksight_namespace(self, namespace: str = "default") -> dict:
        """Create QuickSight namespace."""
        logger.info(f"Creating QuickSight namespace: {namespace}")

        try:
            # Check if namespace exists
            try:
                existing = self.quicksight.describe_namespace(
                    AwsAccountId=self.account_id, Namespace=namespace
                )
                logger.info(f"Namespace already exists: {existing}")
                return {"status": "EXISTS", "namespace": existing}
            except self.quicksight.exceptions.ResourceNotFoundException:
                pass

            # Create namespace
            response = self.quicksight.create_namespace(
                AwsAccountId=self.account_id,
                Namespace=namespace,
                IdentityStore="QUICKSIGHT",
            )

            logger.info(f"Namespace created: {response}")
            return {"status": "CREATED", "namespace": response}

        except ClientError as e:
            logger.error(f"Error creating namespace: {e}")
            raise

    def setup_identity_center_group(
        self, identity_store_id: str, group_name: str
    ) -> dict:
        """Create admin group in Identity Center."""
        logger.info(f"Setting up Identity Center group: {group_name}")

        try:
            # Check if group exists
            groups = self.identity_store.list_groups(
                IdentityStoreId=identity_store_id,
                Filters=[
                    {"AttributePath": "DisplayName", "AttributeValue": group_name}
                ],
            )

            if groups.get("Groups"):
                logger.info(f"Group already exists: {groups['Groups'][0]}")
                return {"status": "EXISTS", "group": groups["Groups"][0]}

            # Create group
            response = self.identity_store.create_group(
                IdentityStoreId=identity_store_id,
                DisplayName=group_name,
                Description=f"Admin group for Quick Suite - {group_name}",
            )

            logger.info(f"Group created: {response}")
            return {"status": "CREATED", "group": response}

        except ClientError as e:
            logger.error(f"Error setting up Identity Center group: {e}")
            raise

    def create_quicksight_service_role(self) -> dict:
        """Create IAM service role for QuickSight."""
        role_name = "QuickSuiteServiceRole"
        logger.info(f"Creating QuickSight service role: {role_name}")

        try:
            # Check if role exists
            try:
                existing = self.iam.get_role(RoleName=role_name)
                logger.info(f"Service role already exists: {existing}")
                return {"status": "EXISTS", "role": existing["Role"]}
            except self.iam.exceptions.NoSuchEntityException:
                pass

            # Create role
            assume_role_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "quicksight.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }

            response = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Description="Service role for Amazon Quick Suite",
                Tags=[{"Key": "ManagedBy", "Value": "QuickSuiteStarterKit"}],
            )

            # Attach managed policies
            managed_policies = [
                "arn:aws:iam::aws:policy/service-role/AWSQuicksightAthenaAccess",
            ]

            for policy_arn in managed_policies:
                try:
                    self.iam.attach_role_policy(
                        RoleName=role_name, PolicyArn=policy_arn
                    )
                except ClientError as e:
                    logger.warning(f"Could not attach policy {policy_arn}: {e}")

            logger.info(f"Service role created: {response}")
            return {"status": "CREATED", "role": response["Role"]}

        except ClientError as e:
            logger.error(f"Error creating service role: {e}")
            raise

    def setup(self, properties: dict) -> dict:
        """Run the complete Quick Suite setup."""
        logger.info(f"Starting Quick Suite setup with properties: {properties}")

        results = {
            "subscription": None,
            "namespace": None,
            "admin_group": None,
            "service_role": None,
        }

        try:
            # 1. Create QuickSight subscription
            results["subscription"] = self.create_quicksight_subscription(
                account_name=properties.get("AccountName", "QuickSuiteStarterKit"),
                admin_email=properties.get("AdminEmail", "admin@example.com"),
            )

            # Wait for subscription to be ready
            time.sleep(5)

            # 2. Create namespace
            results["namespace"] = self.create_quicksight_namespace()

            # 3. Setup Identity Center group
            identity_store_id = properties.get("IdentityStoreId")
            if identity_store_id:
                results["admin_group"] = self.setup_identity_center_group(
                    identity_store_id=identity_store_id,
                    group_name=properties.get("AdminGroupName", "QUICK_SUITE_ADMIN"),
                )

            # 4. Create service role
            results["service_role"] = self.create_quicksight_service_role()

            logger.info(f"Quick Suite setup completed successfully: {results}")
            return {"status": "SUCCESS", "results": results}

        except Exception as e:
            logger.error(f"Quick Suite setup failed: {e}")
            return {"status": "FAILED", "error": str(e), "results": results}

    def cleanup(self, properties: dict) -> dict:
        """Clean up Quick Suite resources."""
        logger.info(f"Starting Quick Suite cleanup with properties: {properties}")

        results = {}

        try:
            # Delete QuickSight subscription
            try:
                self.quicksight.delete_account_subscription(
                    AwsAccountId=self.account_id
                )
                results["subscription"] = "DELETED"
            except ClientError as e:
                logger.warning(f"Could not delete subscription: {e}")
                results["subscription"] = f"FAILED: {e}"

            logger.info(f"Quick Suite cleanup completed: {results}")
            return {"status": "SUCCESS", "results": results}

        except Exception as e:
            logger.error(f"Quick Suite cleanup failed: {e}")
            return {"status": "FAILED", "error": str(e), "results": results}


def handler(event, context):
    """
    Lambda handler for Quick Suite setup.

    Supports both CloudFormation Custom Resource events and direct invocation.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    # Determine request type
    request_type = event.get("RequestType", "Create")
    properties = event.get("ResourceProperties", event.get("properties", {}))

    # Initialize setup handler
    setup = QuickSuiteSetup()

    try:
        if request_type == "Create" or request_type == "Update":
            result = setup.setup(properties)
        elif request_type == "Delete":
            result = setup.cleanup(properties)
        else:
            result = {
                "status": "SKIPPED",
                "message": f"Unknown request type: {request_type}",
            }

        response = {"statusCode": 200, "body": json.dumps(result)}

        logger.info(f"Handler response: {response}")
        return response

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "FAILED", "error": str(e)}),
        }
