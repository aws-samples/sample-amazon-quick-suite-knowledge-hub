#!/bin/bash
# ==============================================================================
# Amazon Quick Security Blog - QuickSight Resource Deployment Script
# ==============================================================================
# This script automates the creation of QuickSight resources for the blog walkthrough.
# It creates datasets, groups, and configures RLS using the AWS CLI.
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - QuickSight Enterprise edition enabled in your account
#   - S3 bucket for uploading datasets
#
# Usage:
#   ./deploy_quicksight_resources.sh --account-id <AWS_ACCOUNT_ID> --bucket <S3_BUCKET>
#
# ==============================================================================

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASETS_DIR="${SCRIPT_DIR}/../datasets"
REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="default"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Parse Arguments ---
ACCOUNT_ID=""
S3_BUCKET=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --account-id) ACCOUNT_ID="$2"; shift 2 ;;
        --bucket) S3_BUCKET="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help)
            echo "Usage: $0 --account-id <AWS_ACCOUNT_ID> --bucket <S3_BUCKET> [--region <REGION>] [--dry-run]"
            exit 0 ;;
        *) log_error "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "$ACCOUNT_ID" || -z "$S3_BUCKET" ]]; then
    log_error "Required: --account-id and --bucket"
    echo "Usage: $0 --account-id <AWS_ACCOUNT_ID> --bucket <S3_BUCKET>"
    exit 1
fi

log_info "Configuration:"
log_info "  Account ID: ${ACCOUNT_ID}"
log_info "  S3 Bucket:  ${S3_BUCKET}"
log_info "  Region:     ${REGION}"
log_info "  Dry Run:    ${DRY_RUN}"
echo ""

# --- Step 1: Upload datasets to S3 ---
log_info "Step 1: Uploading datasets to S3..."

DATASETS=(
    "employee_data.csv"
    "employee_data_manager.csv"
    "employee_data_aggregated.csv"
    "rls-rules.csv"
)

for dataset in "${DATASETS[@]}"; do
    if [[ -f "${DATASETS_DIR}/${dataset}" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "  [DRY RUN] Would upload: ${dataset}"
        else
            aws s3 cp "${DATASETS_DIR}/${dataset}" "s3://${S3_BUCKET}/quicksight-security-blog/${dataset}" --region "${REGION}"
            log_info "  Uploaded: ${dataset}"
        fi
    else
        log_warn "  Missing: ${dataset} (run generate_employee_data.py or create_manager_dataset.py first)"
    fi
done

# --- Step 2: Create QuickSight Groups ---
log_info "Step 2: Creating QuickSight groups..."

GROUPS=("hr-leadership" "dept-managers" "all-employees")

for group in "${GROUPS[@]}"; do
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "  [DRY RUN] Would create group: ${group}"
    else
        aws quicksight create-group \
            --aws-account-id "${ACCOUNT_ID}" \
            --namespace "${NAMESPACE}" \
            --group-name "${group}" \
            --region "${REGION}" 2>/dev/null || log_warn "  Group '${group}' may already exist"
        log_info "  Created group: ${group}"
    fi
done

# --- Step 3: Create DataSource (S3 manifest) ---
log_info "Step 3: Creating S3 data source..."

DATASOURCE_ID="anycompany-s3-source"

# Create manifest file for S3
MANIFEST_FILE=$(mktemp)
cat > "${MANIFEST_FILE}" << EOF
{
    "fileLocations": [
        {"URIs": ["s3://${S3_BUCKET}/quicksight-security-blog/employee_data.csv"]}
    ],
    "globalUploadSettings": {
        "format": "CSV",
        "delimiter": ",",
        "containsHeader": "true"
    }
}
EOF

if [[ "$DRY_RUN" == "true" ]]; then
    log_info "  [DRY RUN] Would create data source: ${DATASOURCE_ID}"
else
    # Upload manifest
    aws s3 cp "${MANIFEST_FILE}" "s3://${S3_BUCKET}/quicksight-security-blog/manifests/employee_data_manifest.json" --region "${REGION}"

    aws quicksight create-data-source \
        --aws-account-id "${ACCOUNT_ID}" \
        --data-source-id "${DATASOURCE_ID}" \
        --name "AnyCompany S3 Source" \
        --type S3 \
        --data-source-parameters "{\"S3Parameters\":{\"ManifestFileLocation\":{\"Bucket\":\"${S3_BUCKET}\",\"Key\":\"quicksight-security-blog/manifests/employee_data_manifest.json\"}}}" \
        --region "${REGION}" 2>/dev/null || log_warn "  Data source may already exist"
    log_info "  Created data source: ${DATASOURCE_ID}"
fi
rm -f "${MANIFEST_FILE}"

# --- Step 4: Create Datasets ---
log_info "Step 4: Creating QuickSight datasets..."

# Full dataset
if [[ "$DRY_RUN" == "true" ]]; then
    log_info "  [DRY RUN] Would create dataset: anycompany-employees-full"
    log_info "  [DRY RUN] Would create dataset: anycompany-employees-manager"
    log_info "  [DRY RUN] Would create dataset: anycompany-employees-aggregated"
    log_info "  [DRY RUN] Would create dataset: anycompany-rls-rules"
else
    log_info "  Datasets must be created via console or CloudFormation for full column mapping."
    log_info "  See scripts/cloudformation/quicksight-datasets.yaml for IaC approach."
fi

# --- Step 5: Share datasets with groups ---
log_info "Step 5: Dataset sharing permissions..."
log_info "  hr-leadership  -> full, manager, aggregated datasets"
log_info "  dept-managers  -> manager (RLS-filtered), aggregated datasets"
log_info "  all-employees  -> aggregated dataset only"

if [[ "$DRY_RUN" != "true" ]]; then
    log_info "  Note: Dataset sharing requires dataset ARNs. Use the console or update this script after dataset creation."
fi

# --- Step 6: Summary ---
echo ""
log_info "==========================================="
log_info " Deployment Summary"
log_info "==========================================="
log_info "Datasets uploaded to: s3://${S3_BUCKET}/quicksight-security-blog/"
log_info "Groups created: ${GROUPS[*]}"
log_info ""
log_info "Next steps (manual in console):"
log_info "  1. Create datasets from S3 source in QuickSight console"
log_info "  2. Apply RLS to the manager dataset using rls-rules.csv"
log_info "  3. Create a Space named 'AnyCompany HR'"
log_info "  4. Upload documents to the Space Knowledge Base"
log_info "  5. Create three Chat Agents (see README for persona prompts)"
log_info "  6. Create the Weekly Attrition Risk Alert Flow"
log_info "  7. Enable CloudTrail for QuickSight events"
log_info "==========================================="
