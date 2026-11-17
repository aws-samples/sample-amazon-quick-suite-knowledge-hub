# Blueprint: locked-down

A secure-by-default governance posture that ships hardened out of the box:

- **READER** role gets a deny-by-default profile (`LockedReader`): view only, with
  optional allowlist exceptions through `reader_allow`.
- **AUTHOR** role gets the `author_no_share` preset (`AuthorNoShare`): can build,
  cannot share externally.
- A KMS customer managed key and a Quick Sight service role are always enabled.

IAM Identity Center is optional (on by default). VPC isolation is off; for network
isolation, use [`idc-vpc-governed`](../idc-vpc-governed/README.md) with the same
profiles. This blueprint is a thin preset over `idc-vpc-governed`.

## When to use

Choose this blueprint when:

- Regulated environments where readers must not author, share, or export data
- You want least-privilege capability defaults without hand-listing every flag

## What it deploys

| Module | Resources |
| --- | --- |
| `identity-center` | Admin group in Identity Center (when `enable_idc = true`) |
| `quick-account` | Account subscription + `default` namespace |
| `kms` | KMS customer managed key registered with Quick Sight |
| `service-roles` | `AmazonQuickServiceRole` for Athena/S3 data access |
| `custom-permissions` | `LockedReader` + `AuthorNoShare` profiles, plus any `extra_profiles` |

## Inputs

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `account_name` | Yes | - | AWS Quick account name |
| `notification_email` | Yes | - | Email for account notifications |
| `identity_store_id` | When `enable_idc = true` | `""` | Identity Store ID |
| `aws_region` | No | `us-east-1` | Region to deploy into |
| `enable_idc` | No | `true` | Integrate with IAM Identity Center |
| `admin_group_name` | No | `QUICK_ADMIN` | Identity Center admin group name |
| `reader_allow` | No | `[]` | Capabilities left permitted for readers, for example `["export_to_pdf"]` |
| `extra_profiles` | No | `{}` | Additional custom-permission profiles to merge in |
| `extra_role_assignments` | No | `{}` | Additional role => profile assignments |
| `tags` | No | `{}` | Tags applied to resources |

## Deploy

Run these commands from the repository root:

```bash
cd infrastructure/quick-terraform/blueprints/locked-down
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply
```

## Outputs

The blueprint returns these outputs:

- `account_name`
- `custom_permission_profiles`
- `kms_key_arn`
- `service_role_arn`
