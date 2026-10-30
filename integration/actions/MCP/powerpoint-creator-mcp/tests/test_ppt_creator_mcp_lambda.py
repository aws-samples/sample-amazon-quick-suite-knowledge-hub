# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import io
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from moto import mock_aws
from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ppt_creator_mcp_lambda as handler  # noqa: E402

BUCKET = "ppt-creator-test"
REGION = "us-west-2"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "template.pptx"


@pytest.fixture
def s3():
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SESSION_TOKEN": "testing",
            "AWS_REGION": REGION,
            "AWS_DEFAULT_REGION": REGION,
            "S3_BUCKET": BUCKET,
            "PRESIGNED_URL_EXPIRY_SECONDS": "900",
        }
    )
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        client.put_object(
            Bucket=BUCKET,
            Key="templates/template.pptx",
            Body=TEMPLATE_PATH.read_bytes(),
        )
        yield client


def _call(arguments):
    event = {
        "httpMethod": "POST",
        "headers": {"Authorization": "Bearer secret-token-value"},
        "body": json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ppt_creator", "arguments": arguments},
            }
        ),
    }
    response = handler.lambda_handler(event, None)
    return json.loads(response["body"])


def _args(**overrides):
    args = {
        "title": "Quarterly plan",
        "subtitle": "Confidential",
        "slides": [{"title": "Secret", "content": "Acquisition target"}],
    }
    args.update(overrides)
    return args


def _keys(s3, prefix):
    return [
        o["Key"]
        for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])
    ]


def test_returns_presigned_url_not_public_url(s3):
    body = _call(_args())
    text = body["result"]["content"][0]["text"]
    url = text.split("): ", 1)[1].split("\n", 1)[0]
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert "cloudfront" not in parsed.netloc
    assert parsed.netloc.startswith(f"{BUCKET}.s3.")
    assert "X-Amz-Signature" in query
    assert query["X-Amz-Expires"] == ["900"]
    assert "expires in 15 minutes" in text


def test_output_keys_are_random_and_do_not_collide(s3):
    _call(_args())
    _call(_args())
    keys = _keys(s3, "presentations/")

    assert len(keys) == 2
    for key in keys:
        parts = key.split("/")
        assert parts[0] == "presentations"
        assert len(parts[1]) == 32
        int(parts[1], 16)


@pytest.mark.parametrize(
    "template_key",
    [
        "presentations/abc/presentation_20261030_102257.pptx",
        "templates/../presentations/x/presentation.pptx",
        "templates/template.pptx/../../secret.pptx",
        "/templates/template.pptx",
        "templates/template.txt",
        "other/template.pptx",
        123,
    ],
)
def test_template_key_outside_templates_prefix_is_rejected(s3, template_key):
    victim = _call(_args())
    assert "result" in victim
    victim_key = _keys(s3, "presentations/")[0]

    if template_key == "presentations/abc/presentation_20261030_102257.pptx":
        template_key = victim_key

    body = _call(_args(template_key=template_key))

    assert body["error"]["code"] == -32602
    assert "template_key" in body["error"]["message"]
    assert _keys(s3, "presentations/") == [victim_key]


def test_custom_template_under_templates_prefix_is_used(s3):
    prs = Presentation(io.BytesIO(TEMPLATE_PATH.read_bytes()))
    buf = io.BytesIO()
    prs.save(buf)
    s3.put_object(Bucket=BUCKET, Key="templates/brand/v2.pptx", Body=buf.getvalue())

    body = _call(_args(template_key="templates/brand/v2.pptx"))

    assert "result" in body


def test_internal_errors_are_not_leaked(s3):
    s3.delete_object(Bucket=BUCKET, Key="templates/template.pptx")
    os.environ["S3_BUCKET"] = "bucket-that-does-not-exist"
    try:
        body = _call(_args())
    finally:
        os.environ["S3_BUCKET"] = BUCKET

    assert body["error"] == {"code": -32603, "message": "Failed to create presentation"}


def test_invalid_arguments_are_rejected(s3):
    body = _call({"title": None, "subtitle": "x", "slides": []})

    assert body["error"]["code"] == -32602


def test_events_tokens_and_urls_are_not_logged(s3, caplog):
    with caplog.at_level(logging.DEBUG):
        body = _call(_args())

    assert "result" in body
    logged = caplog.text
    assert "secret-token-value" not in logged
    assert "X-Amz-Signature" not in logged
    assert "Acquisition target" not in logged


def test_parse_error_does_not_echo_input(s3):
    response = handler.lambda_handler(
        {"httpMethod": "POST", "body": "{not json secret-token-value"}, None
    )
    body = json.loads(response["body"])

    assert body["error"] == {"code": -32700, "message": "Parse error"}
