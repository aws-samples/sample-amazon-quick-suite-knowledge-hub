#!/bin/bash
# Create IAM role for the Document Skills AgentCore Runtime agent.
#
# This role is used by AgentCore Runtime to invoke Bedrock models,
# use Code Interpreter, and write CloudWatch logs.
#
# Usage:
#   export AWS_REGION=us-west-2   # optional, defaults to us-west-2
#   ./create-iam-role.sh
set -euo pipefail

ROLE_NAME="DocumentSkillsAgentCoreRole"
AWS_REGION=${AWS_REGION:-us-west-2}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "==> Account: $ACCOUNT_ID | Region: $AWS_REGION"
echo "==> Creating IAM role: $ROLE_NAME"

# Trust policy for AgentCore Runtime
cat > /tmp/trust-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "bedrock-agentcore.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file:///tmp/trust-policy.json \
    --description "Role for Document Skills agent on AgentCore Runtime" \
    2>/dev/null || echo "Role already exists"

# Permissions: Bedrock models (Claude + Nova via cross-region inference),
# Code Interpreter, and CloudWatch Logs.
cat > /tmp/permissions-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BedrockModelAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                "arn:aws:bedrock:us:${ACCOUNT_ID}:inference-profile/*",
                "arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:inference-profile/*"
            ]
        },
        {
            "Sid": "CodeInterpreterAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateCodeInterpreter",
                "bedrock-agentcore:StartCodeInterpreterSession",
                "bedrock-agentcore:InvokeCodeInterpreter",
                "bedrock-agentcore:StopCodeInterpreterSession",
                "bedrock-agentcore:DeleteCodeInterpreter",
                "bedrock-agentcore:ListCodeInterpreters",
                "bedrock-agentcore:GetCodeInterpreter",
                "bedrock-agentcore:GetCodeInterpreterSession",
                "bedrock-agentcore:ListCodeInterpreterSessions"
            ],
            "Resource": [
                "arn:aws:bedrock-agentcore:${AWS_REGION}:aws:code-interpreter/*",
                "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:code-interpreter-custom/*"
            ]
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:*"
        },
        {
            "Sid": "DirectPersistS3",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::document-skills-output-${ACCOUNT_ID}-${AWS_REGION}/*"
        },
        {
            "Sid": "DirectPersistDynamoDB",
            "Effect": "Allow",
            "Action": [
                "dynamodb:UpdateItem"
            ],
            "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/document-skill-jobs"
        }
    ]
}
EOF

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "DocumentSkillsPermissions" \
    --policy-document file:///tmp/permissions-policy.json

echo "==> Role ARN:"
aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text

rm -f /tmp/trust-policy.json /tmp/permissions-policy.json
echo "==> Done. Use this role ARN when running 'agentcore deploy' for the first time."
