# Knowledge Base — IAM & S3 Setup

This document describes the IAM policy that must be attached to the **QuickSight
service role** so that S3-backed knowledge bases work, and explains the S3
bucket naming convention used by the scripts.

---

## 1. Bucket naming convention

Every S3 bucket created for a knowledge base **must** start with the prefix
`knowledge-base-`. The scripts build the name as:

```
knowledge-base-<env>-<account_id>
```

Examples:
- `knowledge-base-dev-111122223333`
- `knowledge-base-prod-444455556666`

The `<env>` segment (e.g. `dev`, `prod`) is passed to the scripts:
- `setup_source.py` → `--env dev`
- MCP migrator `migrate_spaces` tool → `source_env` / `target_env` inputs

This naming lets a **single wildcard IAM statement** (`knowledge-base-*`) grant
access to every environment's bucket, instead of hard-coding one bucket per
environment.

---

## 2. Policy to attach to the QuickSight service role

Attach the following policy to the QuickSight service role
(default: `aws-quicksight-service-role-v0`). Note the `knowledge-base-*`
wildcard — the `*` matches the environment + account portion of the bucket
name (`dev`, `prod`, etc.), so you do **not** need to update this policy each
time a new environment bucket is created.

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:ListAllMyBuckets",
            "Resource": "arn:aws:s3:::*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::knowledge-base-*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:GetObjectVersion"
            ],
            "Resource": [
                "arn:aws:s3:::knowledge-base-*/*"
            ]
        }
    ]
}
```

> **Note (CloudFormation IaC):** when managing this as IaC, define it as an
> `AWS::IAM::ManagedPolicy` and attach it to the role via `ManagedPolicyArns`
> (do not use an inline `Policies:` block).

---

## 3. Per-bucket policy (applied automatically by the scripts)

In addition to the role policy above, each bucket gets a **bucket policy**
granting the QuickSight service role read access. The scripts apply this
automatically at bucket-creation time — shown here for reference (with a real
bucket name substituted):

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowQuick",
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::<ACCOUNT_ID>:role/service-role/aws-quicksight-service-role-v0"
            },
            "Action": [
                "s3:GetObject",
                "s3:ListBucket",
                "s3:GetBucketLocation",
                "s3:GetObjectVersion",
                "s3:ListBucketVersions"
            ],
            "Resource": [
                "arn:aws:s3:::knowledge-base-<env>-<ACCOUNT_ID>",
                "arn:aws:s3:::knowledge-base-<env>-<ACCOUNT_ID>/*"
            ]
        }
    ]
}
```

The QuickSight service-role name is **not discoverable via any API**, so it is
taken as an explicit input:
- `setup_source.py` → `--qs-service-role aws-quicksight-service-role-v0`
- MCP migrator `migrate_spaces` tool → `qs_service_role` input (defaults to
  `aws-quicksight-service-role-v0`)

---

## 4. What the scripts do with S3 + knowledge bases

**`setup_source.py`**
1. Creates `knowledge-base-<env>-<account>` bucket + attaches the bucket policy
2. Creates an `S3_KNOWLEDGE_BASE` data source pointing at `s3://<bucket>`
3. Creates the knowledge base (S3V2 template, media extraction enabled)
4. Grants the 14 KB permissions to the principal
5. Attaches the knowledge base to the space

**MCP migrator (`migrate_spaces`)**
1. Discovers knowledge bases attached to each source space
2. Creates the target bucket `knowledge-base-<target_env>-<target_account>` + policy
3. Recreates the data source + knowledge base in the target account
4. **Copies KB permissions** by describing the source KB permissions and
   replicating the exact actions in the target (principals remapped to the
   target account)
5. Attaches the migrated knowledge base to the target space

> **Data note:** the migrator provisions the target bucket and registers the
> knowledge base against it, but it does **not** copy the S3 objects
> (documents) themselves. Copy the objects from the source bucket to the target
> bucket (e.g. `aws s3 sync s3://knowledge-base-dev-<src> s3://knowledge-base-prod-<tgt>`)
> before or after migration, then trigger a KB ingestion.
