# Blueprint: vpc-no-idc

Network-isolated AWS Quick using native Quick Sight identity (no IAM Identity
Center). Enables a PrivateLink interface endpoint (Type 1) and/or a Quick Sight
VPC connection (Type 2), plus a KMS customer managed key and a service role.

This is a thin preset over [`idc-vpc-governed`](../idc-vpc-governed/README.md)
with Identity Center turned off.

## When to use

Choose this blueprint when:

- AWS Quick traffic must stay on private networking
- Data sources live inside a VPC (RDS, Redshift, private endpoints)
- You are not using IAM Identity Center for AWS Quick sign-in

## What it deploys

| Module | Resources |
| --- | --- |
| `quick-account` | Account subscription (`IAM_AND_QUICKSIGHT`) + `default` namespace |
| `vpc-access` | PrivateLink endpoint (Type 1) and/or Quick Sight VPC connection (Type 2) |
| `kms` | KMS customer managed key registered with Quick Sight (toggle) |
| `service-roles` | `AmazonQuickServiceRole` for Athena/S3 data access |

## Inputs

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `account_name` | Yes | - | AWS Quick account name |
| `notification_email` | Yes | - | Email for account notifications |
| `vpc_id` | Yes | - | VPC to attach to |
| `subnet_ids` | Yes | - | Subnets for the endpoint / VPC connection |
| `security_group_ids` | Yes | - | Security groups for the endpoint / VPC connection |
| `aws_region` | No | `us-east-1` | Region to deploy into |
| `enable_privatelink` | No | `true` | Create the PrivateLink interface endpoint |
| `enable_vpc_connection` | No | `true` | Create the Quick Sight VPC connection |
| `privatelink_service_name` | No | `""` | Region-specific endpoint service name, for example `com.amazonaws.us-east-1.quicksight-api` |
| `enable_kms` | No | `true` | Create and register a KMS key |
| `tags` | No | `{}` | Tags applied to resources |

## Deploy

Run these commands from the repository root:

```bash
cd infrastructure/quick-terraform/blueprints/vpc-no-idc
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply
```

## Outputs

The blueprint returns these outputs:

- `account_name`
- `privatelink_endpoint_id`
- `vpc_connection_id`
- `kms_key_arn`
