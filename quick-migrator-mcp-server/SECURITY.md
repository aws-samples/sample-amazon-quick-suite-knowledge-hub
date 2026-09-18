# Security Policy

## Disclaimer

This project is provided as sample/educational code and is **NOT intended for production
use without additional security hardening**. See the Production Hardening Recommendations
section below before deploying in any environment that processes real data.

## Reporting Vulnerabilities

If you discover a security vulnerability in this project, please report it by emailing
aws-security@amazon.com. Do **not** create a public GitHub issue for security vulnerabilities.

## AWS Services Used

- **Amazon Bedrock AgentCore Runtime** — hosts the MCP server (src/server.py)
- **Amazon Cognito** — issues JWT tokens for machine-to-machine authentication
- **Amazon QuickSight** — source and target for migrated Spaces, Agents, Connectors, and Knowledge Bases
- **Amazon S3** — stores the deployment artifact and knowledge base documents
- **AWS IAM** — cross-account role assumption (source and target migrator roles)
- **AWS STS** — role assumption for cross-account access
- **Amazon VPC** — network isolation for the AgentCore runtime

## Prerequisites and Permissions

To deploy this solution you need:
- AWS CLI v2 configured with credentials for three accounts (runner, source, target)
- IAM permissions to create roles, managed policies, Cognito user pools, VPCs, and AgentCore runtimes
- An existing S3 bucket for deployment artifacts

## Known Security Considerations

| Item | Category | Rationale |
|------|----------|-----------|
| `Resource: "*"` on ~55 QuickSight account-level actions | Security Debt | These QuickSight APIs do not support resource-level permissions per the AWS Service Authorization Reference. QuickSight enforces account-level scoping at the API layer. |
| S3 artifact bucket uses SSE-S3 (AES256), not SSE-KMS | Security Debt | Acceptable for sample deployment artifacts. Production deployments should use a customer-managed KMS key. |
| Knowledge-base S3 buckets lack DenyInsecureTransport bucket policy | Security Debt | Access is exclusively via the QuickSight service role (always HTTPS). A DenyInsecureTransport statement should be added for production use. |
| No VPC Flow Logs | Security Debt | Flow logs are a monitoring best practice. Not enabled to reduce sample deployment complexity. |
| Cognito user pool lacks AdvancedSecurityMode | Security Debt | This pool uses client_credentials grant only (M2M). AdvancedSecurityMode applies to interactive user sign-in and has no effect on machine-to-machine flows. |

## Production Hardening Recommendations

Before using this in a production environment:

1. **S3 encryption**: Update `deploy.sh` to configure the artifact bucket with `SSEAlgorithm: aws:kms` and a dedicated KMS key with key rotation enabled.
2. **Knowledge-base bucket policy**: Add a `DenyInsecureTransport` statement to `bucket_policy_for_quicksight()` in `server.py` and `setup_source.py`.
3. **Knowledge-base public access block**: Add `s3.put_public_access_block(Bucket=bucket_name, PublicAccessBlockConfiguration={...all True...})` to `create_kb_bucket()`.
4. **Deletion protection**: Add `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain` to the Cognito UserPool, UserPoolClient, VPC, and NAT Gateway in the CloudFormation templates.
5. **VPC Flow Logs**: Add an `AWS::EC2::FlowLog` resource to `network.yaml`.
6. **Input validation**: Add format validation (regex) on `source_account_id`, `target_account_id`, and `region` in the MCP tool handlers before passing to boto3.
7. **IAM quarterly review**: Re-check the `QuickSightMigrationAccountLevel` action list against the AWS Service Authorization Reference periodically to scope newly supported actions to resource ARNs.

## Resource Cleanup

To remove all deployed resources:

```bash
# Delete runtime + Cognito stack
aws cloudformation delete-stack --stack-name quick-space-migrator --region us-east-1

# Delete network stack
aws cloudformation delete-stack --stack-name quick-migrator-network --region us-east-1

# Delete source/target roles (run in each account)
aws cloudformation delete-stack --stack-name quick-space-migrator-role-stack --region us-east-1

# Delete runner role stack (central account)
aws cloudformation delete-stack --stack-name quick-space-migrator-runner-role --region us-east-1
```

Note: S3 buckets (artifact and knowledge-base-*) must be emptied before the stacks can delete them.

## Dependencies

| Dependency | Version constraint | Notes |
|------------|-------------------|-------|
| boto3 | >=1.35.0 | AWS SDK — no known vulnerabilities at time of publication |
| botocore | >=1.35.0 | AWS SDK core — no known vulnerabilities at time of publication |
| mcp | >=1.10.0,<2.0.0 | MCP Python SDK — min 1.10.0 fixes CVE-2025-53365 / CVE-2025-53366 (DoS); pinned <2.0.0 as mcp 2.x removed the fastmcp module used by this server |
| aws-opentelemetry-distro | >=0.8.0 | AWS OTel instrumentation |

Run `pip-audit` or `safety check` after resolving dependencies to verify no CVEs at your specific resolved versions.
