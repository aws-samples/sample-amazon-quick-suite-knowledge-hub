#!/usr/bin/env bash
#
# deploy-network.sh — Deploy the VPC networking stack for the AgentCore runtime.
#
# Creates: VPC, 2 public + 2 private subnets, IGW, NAT gateway, S3 gateway
# endpoint, STS + Logs interface endpoints, and the runtime/endpoint SGs.
# Exports private subnet IDs + runtime SG for deploy.sh to consume.
#
# IMPORTANT — Availability Zones:
#   AgentCore only supports specific AZ *IDs* (us-east-1: use1-az1/az2/az4).
#   CloudFormation subnets take AZ *names* (us-east-1a, ...), and the
#   name→ID mapping differs per account. Verify before deploying:
#     aws ec2 describe-availability-zones --region us-east-1 \
#       --query "AvailabilityZones[].[ZoneName,ZoneId]" --output table
#   Pick two AZ names whose IDs are in the supported set.
#
# Usage:
#   ./deploy-network.sh --az1 us-east-1a --az2 us-east-1b \
#     [--region us-east-1] [--stack-name quick-migrator-network] \
#     [--name-prefix quick-migrator]
#     [--assume-role-arn arn:aws:iam::<runner>:role/deployer] [--profile BASE]
#   Precedence: --assume-role-arn > --profile > ambient/default creds.
#
set -euo pipefail

REGION="us-east-1"
STACK_NAME="quick-migrator-network"
NAME_PREFIX="quick-migrator"
AZ1=""
AZ2=""
ASSUME_ROLE_ARN=""
PROFILE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${REPO_ROOT}/infrastructure/network.yaml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --az1)          AZ1="$2"; shift 2 ;;
    --az2)          AZ2="$2"; shift 2 ;;
    --region)       REGION="$2"; shift 2 ;;
    --stack-name)   STACK_NAME="$2"; shift 2 ;;
    --name-prefix)  NAME_PREFIX="$2"; shift 2 ;;
    --assume-role-arn) ASSUME_ROLE_ARN="$2"; shift 2 ;;
    --profile)      PROFILE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

: "${AZ1:?--az1 is required (e.g. us-east-1a — must map to a supported AZ ID)}"
: "${AZ2:?--az2 is required (e.g. us-east-1b — must map to a supported AZ ID)}"

# ── Credentials ─────────────────────────────────────────────────────
# Precedence: --assume-role-arn > --profile > ambient/default creds. Resolved
# once here so every aws call below runs as the right identity.
if [[ -n "${ASSUME_ROLE_ARN}" ]]; then
  echo "→ Assuming role ${ASSUME_ROLE_ARN}..."
  _base_prof=""; [[ -n "${PROFILE}" ]] && _base_prof="--profile ${PROFILE}"
  _creds="$(aws sts assume-role ${_base_prof} --region "${REGION}" \
    --role-arn "${ASSUME_ROLE_ARN}" --role-session-name "deploy-network" \
    --query "Credentials.[AccessKeyId,SecretAccessKey,SessionToken]" --output text)"
  if [[ -z "${_creds}" || "${_creds}" == *None* ]]; then
    echo "✗ Failed to assume ${ASSUME_ROLE_ARN}"; exit 1
  fi
  unset AWS_PROFILE
  export AWS_ACCESS_KEY_ID="$(echo "${_creds}" | awk '{print $1}')"
  export AWS_SECRET_ACCESS_KEY="$(echo "${_creds}" | awk '{print $2}')"
  export AWS_SESSION_TOKEN="$(echo "${_creds}" | awk '{print $3}')"
  echo "   ✓ Assumed role credentials active"
elif [[ -n "${PROFILE}" ]]; then
  export AWS_PROFILE="${PROFILE}"
fi

echo "════════════════════════════════════════════════════════"
echo "  Quick Resource Migrator — network stack"
echo "  Region: ${REGION}   Stack: ${STACK_NAME}"
echo "  AZs:    ${AZ1}, ${AZ2}"
echo "════════════════════════════════════════════════════════"

# Show the AZ name→ID mapping so the user can confirm supported AZ IDs
echo "→ AZ name → ID mapping for ${REGION} (confirm IDs are AgentCore-supported):"
aws ec2 describe-availability-zones --region "${REGION}" \
  --query "AvailabilityZones[?ZoneName=='${AZ1}' || ZoneName=='${AZ2}'].[ZoneName,ZoneId]" \
  --output table || true

# Look up the S3 gateway-endpoint managed prefix list ID for this region
echo "→ Looking up S3 prefix list ID for ${REGION}..."
S3_PREFIX_LIST_ID="$(aws ec2 describe-prefix-lists --region "${REGION}" \
  --filters "Name=prefix-list-name,Values=com.amazonaws.${REGION}.s3" \
  --query "PrefixLists[0].PrefixListId" --output text)"
if [[ -z "${S3_PREFIX_LIST_ID}" || "${S3_PREFIX_LIST_ID}" == "None" ]]; then
  echo "✗ Could not resolve S3 prefix list ID for ${REGION}"; exit 1
fi
echo "   S3 prefix list: ${S3_PREFIX_LIST_ID}"

aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE}" \
  --parameter-overrides \
    "NamePrefix=${NAME_PREFIX}" \
    "AvailabilityZone1=${AZ1}" \
    "AvailabilityZone2=${AZ2}" \
    "S3PrefixListId=${S3_PREFIX_LIST_ID}"

echo ""
echo "→ Network stack outputs:"
aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK_NAME}" \
  --query "Stacks[0].Outputs" --output table

echo ""
echo "✓ Network ready. Now run deploy.sh with:  --network-stack ${STACK_NAME}"
