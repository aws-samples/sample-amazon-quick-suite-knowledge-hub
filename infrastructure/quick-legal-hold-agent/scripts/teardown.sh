#!/usr/bin/env bash
#
# teardown.sh - remove the STANDALONE quick-legalhold-mcp stack and all its own infra.
#
# This stack now owns ALL its infrastructure (KMS, S3 WORM bucket, DynamoDB, Firehose,
# filter + hold-manager + interceptor Lambdas, Cognito pool/domain/clients, 3LO test user
# + secret, AgentCore Gateway + target). Nothing is shared with quick-legalhold-test.
#
# ORDER MATTERS for the WORM bucket (Object Lock GOVERNANCE + versioning):
#   1. Remove S3 Legal Hold on every object version.
#   2. Delete all versions + delete markers with --bypass-governance-retention.
#   3. cdk destroy (also removes Cognito pool, user, secret, gateway, etc.).
#
# GOVERNANCE mode (never Compliance) means an admin can remove holds + versions, so
# everything is deletable. Safe to re-run. Does NOT touch quick-legalhold-test.
#
set -uo pipefail
cd "$(dirname "$0")/.."

STACK="${STACK:-quick-legalhold-mcp}"
REGION="${AWS_REGION:-us-east-1}"

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }

BUCKET="$(out WormBucketName)"
echo "==> Teardown for stack '$STACK' in $REGION"
echo "    WORM bucket: ${BUCKET:-<not found>}"

if [[ -n "${BUCKET:-}" && "$BUCKET" != "None" ]]; then
  echo "==> Step 1/3: remove Legal Hold on every object version"
  aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" --output json 2>/dev/null \
    | python3 - "$BUCKET" "$REGION" <<'PY'
import json, subprocess, sys
data = sys.stdin.read().strip()
if not data: sys.exit(0)
bucket, region = sys.argv[1], sys.argv[2]
doc = json.loads(data)
for v in doc.get("Versions", []):
    subprocess.run(["aws","s3api","put-object-legal-hold","--bucket",bucket,"--region",region,
        "--key",v["Key"],"--version-id",v["VersionId"],"--legal-hold","Status=OFF"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("legal holds cleared (if any)")
PY

  echo "==> Step 2/3: delete all versions + delete markers (bypass Governance)"
  python3 - "$BUCKET" "$REGION" <<'PY'
import json, subprocess, sys
bucket, region = sys.argv[1], sys.argv[2]
raw = subprocess.run(["aws","s3api","list-object-versions","--bucket",bucket,"--region",region,"--output","json"],
    capture_output=True, text=True)
if raw.returncode != 0 or not raw.stdout.strip(): sys.exit(0)
doc = json.loads(raw.stdout)
objs = [{"Key":v["Key"],"VersionId":v["VersionId"]} for v in doc.get("Versions",[])]
objs += [{"Key":m["Key"],"VersionId":m["VersionId"]} for m in doc.get("DeleteMarkers",[])]
for i in range(0, len(objs), 1000):
    chunk = objs[i:i+1000]
    subprocess.run(["aws","s3api","delete-objects","--bucket",bucket,"--region",region,
        "--bypass-governance-retention","--delete", json.dumps({"Objects":chunk,"Quiet":True})],
        capture_output=True, text=True)
print(f"deleted {len(objs)} versions/markers")
PY
else
  echo "==> Bucket not found; skipping S3 cleanup."
fi

echo "==> Step 3/3: cdk destroy"
npx cdk destroy --force

echo "==> Teardown complete. quick-legalhold-test was NOT touched."
