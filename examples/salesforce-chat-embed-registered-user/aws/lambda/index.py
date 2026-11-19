"""
Mints a single-use Amazon Quick chat agent embed URL for a registered Quick user.

Invoked by API Gateway (REST, Lambda proxy integration, API key required).

Request body:  {"userName": "<Quick registered user name, typically the email>"}
Response 200:  {"embedUrl": "https://..."}
Response 4xx/5xx: {"message": "..."}
"""

import base64
import json
import logging
import os
import re

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ACCOUNT_ID = os.environ["QUICK_ACCOUNT_ID"]
NAMESPACE = os.environ.get("QUICK_NAMESPACE", "default")
# Region that hosts Quick users (identity region). Often us-east-1.
IDENTITY_REGION = os.environ.get("QUICK_IDENTITY_REGION") or os.environ["AWS_REGION"]
# Region that hosts the Quick chat agent experience.
EMBED_REGION = os.environ.get("QUICK_EMBED_REGION") or os.environ["AWS_REGION"]
ALLOWED_DOMAINS = [
    d.strip() for d in os.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()
]
SESSION_LIFETIME_MINUTES = int(os.environ.get("SESSION_LIFETIME_MINUTES", "600"))

# Quick user names: emails or IAM-style names. Strict, to reject junk early.
USER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_/\-]{1,256}$")

identity_client = boto3.client("quicksight", region_name=IDENTITY_REGION)
embed_client = boto3.client("quicksight", region_name=EMBED_REGION)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def _parse_user_name(event):
    raw = event.get("body") or "{}"
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    user_name = payload.get("userName") if isinstance(payload, dict) else None
    if not isinstance(user_name, str):
        return None
    user_name = user_name.strip()
    return user_name if USER_NAME_PATTERN.match(user_name) else None


def handler(event, _context):
    user_name = _parse_user_name(event)
    if not user_name:
        return _response(
            400, {"message": "Request body must be JSON with a valid 'userName'."}
        )

    try:
        user = identity_client.describe_user(
            AwsAccountId=ACCOUNT_ID, Namespace=NAMESPACE, UserName=user_name
        )["User"]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return _response(
                404,
                {
                    "message": f"No Quick user named '{user_name}' in namespace '{NAMESPACE}'."
                },
            )
        logger.exception("describe_user failed")
        return _response(502, {"message": f"Could not look up Quick user ({code})."})

    params = {
        "AwsAccountId": ACCOUNT_ID,
        "UserArn": user["Arn"],
        "SessionLifetimeInMinutes": SESSION_LIFETIME_MINUTES,
        "ExperienceConfiguration": {"QuickChat": {}},
    }
    if ALLOWED_DOMAINS:
        params["AllowedDomains"] = ALLOWED_DOMAINS

    try:
        embed_url = embed_client.generate_embed_url_for_registered_user(**params)[
            "EmbedUrl"
        ]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        logger.exception("generate_embed_url_for_registered_user failed")
        return _response(502, {"message": f"Could not generate embed URL ({code})."})

    # Never log the embed URL: it is a bearer credential until first use.
    logger.info("Minted Quick chat embed URL for user %s", user_name)
    return _response(200, {"embedUrl": embed_url})
