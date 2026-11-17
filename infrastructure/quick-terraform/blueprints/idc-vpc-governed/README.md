# Blueprint: idc-vpc-governed

The full-featured superset blueprint. It composes every reusable module under
`modules/`, and each capability is a feature flag. Turning flags off reproduces
the simpler topologies, so [`idc-centralized`](../idc-centralized/README.md),
[`vpc-no-idc`](../vpc-no-idc/README.md), and [`locked-down`](../locked-down/README.md)
are thin presets over this blueprint.

## When to use

Choose this blueprint when:

- You need Identity Center, VPC isolation, and governance together
- You want one blueprint where every capability can be toggled
- You need account governance: termination protection, IP/VPC access restrictions,
  or IAM policy assignments

## What it deploys

| Module | Flag | Resources |
| --- | --- | --- |
| `identity-center` | `enable_idc` | Admin group in Identity Center |
| `quick-account` | always | Account subscription + `default` namespace |
| `kms` | `enable_kms` | KMS customer managed key registered with Quick Sight |
| `service-roles` | `enable_service_role` | `AmazonQuickServiceRole` for Athena/S3 data access |
| `vpc-access` | `enable_privatelink`, `enable_vpc_connection` | PrivateLink endpoint (Type 1), Quick Sight VPC connection (Type 2) |
| `custom-permissions` | `enable_custom_permissions` | Custom-permission profiles + role bindings |
| `account-governance` | `enable_account_settings`, `enable_ip_restriction`, `iam_policy_assignments` | Account settings, IP/VPC restrictions, IAM policy assignments |

## Key inputs

All inputs are defined in `variables.tf`. The most important ones:

| Variable | Default | Description |
| --- | --- | --- |
| `account_name` | - (required) | AWS Quick account name |
| `notification_email` | - (required) | Email for account notifications |
| `aws_region` | `us-east-1` | Region to deploy into |
| `edition` | `ENTERPRISE` | AWS Quick edition |
| `enable_idc` / `identity_store_id` | `true` / `""` | Identity Center integration (ID required when enabled) |
| `enable_kms` / `kms_multi_region` | `true` / `false` | KMS key; set multi-Region when the IDC home Region differs from `aws_region` |
| `enable_privatelink` / `enable_vpc_connection` | `false` / `false` | VPC access; requires `vpc_id`, `subnet_ids`, `security_group_ids` |
| `enable_service_role` / `service_role_s3_bucket_arns` | `true` / `[]` | Service role and the S3 buckets it can read |
| `enable_custom_permissions` | `false` | Custom-permission profiles and role assignments |
| `enable_account_settings` / `termination_protection_enabled` | `false` / `true` | Account-wide settings |
| `enable_ip_restriction` / `ip_restriction_rule_map` | `false` / `{}` | IP / VPC / VPC endpoint allowlists |
| `iam_policy_assignments` | `{}` | Bind IAM managed policies to Quick Sight users/groups |

## Deploy

Run these commands from the repository root:

```bash
cd infrastructure/quick-terraform/blueprints/idc-vpc-governed
cp terraform.tfvars.example terraform.tfvars   # edit values, turn off what you do not need
terraform init
terraform plan
terraform apply
```

## Outputs

`account_name`, `account_subscription_status`, `namespace`, `idc_group_ids`,
`service_role_arn`, `kms_key_arn`, `privatelink_endpoint_id`, `vpc_connection_id`,
`custom_permission_profiles`, `termination_protection_enabled`,
`ip_restriction_enabled`, `iam_policy_assignment_names`.
