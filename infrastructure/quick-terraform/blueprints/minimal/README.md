# Blueprint: minimal

The smallest possible AWS Quick deployment: an account subscription and the
`default` namespace, using native Quick Sight identity (`IAM_AND_QUICKSIGHT`).
No IAM Identity Center, no VPC, no KMS key, and no custom permissions.

## When to use

Choose this blueprint when:

- Evaluating AWS Quick in a sandbox or proof-of-concept account
- You do not need Identity Center, network isolation, or capability governance yet

Need more later? Move to [`idc-centralized`](../idc-centralized/README.md),
[`vpc-no-idc`](../vpc-no-idc/README.md), or the full
[`idc-vpc-governed`](../idc-vpc-governed/README.md) blueprint.

## What it deploys

| Module | Resources |
| --- | --- |
| `quick-account` | AWS Quick account subscription + `default` namespace |

## Inputs

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `account_name` | Yes | - | AWS Quick account name |
| `notification_email` | Yes | - | Email for account notifications |
| `aws_region` | No | `us-east-1` | Region to deploy into |
| `edition` | No | `ENTERPRISE` | AWS Quick edition |
| `tags` | No | `{}` | Tags applied to resources |

## Deploy

Run these commands from the repository root:

```bash
cd infrastructure/quick-terraform/blueprints/minimal
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply
```

## Outputs

The blueprint returns these outputs:

- `account_name`
- `account_subscription_status`
- `namespace`
