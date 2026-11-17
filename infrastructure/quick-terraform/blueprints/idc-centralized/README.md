# Blueprint: idc-centralized

The recommended centralized enterprise setup. AWS Quick is integrated with
IAM Identity Center, with a KMS customer managed key, a Quick Sight service role,
and custom-permission profiles bound to Quick Sight roles. No VPC isolation.

This is a thin preset over [`idc-vpc-governed`](../idc-vpc-governed/README.md)
with the VPC features turned off.

## When to use

Choose this blueprint when:

- Your users sign in through IAM Identity Center
- You want capability governance (for example, locked-down readers) from day one
- Network isolation is not required, or you will add it later with
  [`idc-vpc-governed`](../idc-vpc-governed/README.md)

## What it deploys

| Module | Resources |
| --- | --- |
| `identity-center` | Admin group in Identity Center (created or referenced) |
| `quick-account` | Account subscription (`IAM_IDENTITY_CENTER`) + `default` namespace |
| `kms` | KMS customer managed key registered with Quick Sight (toggle) |
| `service-roles` | `AmazonQuickServiceRole` for Athena/S3 data access |
| `custom-permissions` | Custom-permission profiles + role bindings (toggle) |

## Inputs

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `account_name` | Yes | - | AWS Quick account name |
| `notification_email` | Yes | - | Email for account notifications |
| `identity_store_id` | Yes | - | Identity Store ID (`aws sso-admin list-instances`) |
| `aws_region` | No | `us-east-1` | Region to deploy into |
| `admin_group_name` | No | `QUICK_ADMIN` | Identity Center admin group name |
| `create_admin_group` | No | `true` | Create the group, or reference an existing one |
| `enable_kms` | No | `true` | Create and register a KMS key |
| `enable_custom_permissions` | No | `true` | Create custom-permission profiles |
| `custom_permission_profiles` | No | `{}` | Profile name => `{ deny_by_default, preset, allow, capabilities }` |
| `custom_permission_role_assignments` | No | `{}` | Quick Sight role => profile name |
| `tags` | No | `{}` | Tags applied to resources |

## Deploy

Run these commands from the repository root:

```bash
cd infrastructure/quick-terraform/blueprints/idc-centralized
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply
```

## Outputs

The blueprint returns these outputs:

- `account_name`
- `idc_group_ids`
- `service_role_arn`
- `kms_key_arn`
- `custom_permission_profiles`
