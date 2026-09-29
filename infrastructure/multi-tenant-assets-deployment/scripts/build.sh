#!/usr/bin/env bash
#
# build.sh — Package the Quick Resource Migrator MCP server for Amazon Bedrock AgentCore.
#
# Installs dependencies for Python 3.14 with Linux aarch64 (ARM64) compatibility
# — AgentCore Runtime runs on Graviton (Linux ARM64), so native wheels
# (pydantic_core, etc.) MUST be aarch64 manylinux wheels, not macOS/x86 ones.
#
# Layout (relative to the repository root):
#   src/*.py              — the MCP server source (server.py + flat sibling
#                           modules: common, resources, backups, migrate_*)
#   requirements.txt      — runtime dependencies
#   build/                — scratch dir for vendored deps (git-ignored)
#   build/deployment.zip  — the artifact uploaded to AgentCore
#
# Usage:
#   ./scripts/build.sh
#
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────
PY_VERSION="3.14"
PLATFORM="manylinux2014_aarch64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
ZIP_PATH="${BUILD_DIR}/deployment.zip"

echo "════════════════════════════════════════════════════════"
echo "  Quick Resource Migrator — AgentCore build"
echo "  Python ${PY_VERSION}  |  Platform ${PLATFORM}"
echo "════════════════════════════════════════════════════════"

# ── 1. Reset the build dir ──────────────────────────────────────────
echo "→ Preparing clean build dir..."
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# ── 2. Copy first-party source ──────────────────────────────────────
# server.py imports its helpers from flat sibling modules (common, resources,
# backups, migrate_*) by bare name. AgentCore flattens the bundle root onto
# sys.path, so ALL src/*.py must sit next to server.py at the build root.
echo "→ Copying server source (all src/*.py)..."
cp "${REPO_ROOT}"/src/*.py "${BUILD_DIR}/"
echo "    copied: $(cd "${REPO_ROOT}/src" && ls *.py | tr '\n' ' ')"

# ── 3. Install dependencies targeting Linux ARM64 / Python 3.14 ─────
# --platform + --only-binary=:all: forces pip to fetch aarch64 manylinux
# wheels regardless of the host OS/arch (works on Intel or Apple-Silicon Mac).
echo "→ Installing dependencies (Linux aarch64, py${PY_VERSION})..."
python3 -m pip install \
  --platform "${PLATFORM}" \
  --implementation cp \
  --python-version "${PY_VERSION}" \
  --only-binary=:all: \
  --upgrade \
  --target "${BUILD_DIR}" \
  -r "${REPO_ROOT}/requirements.txt"

# ── 4. Zip everything at the build/ root ────────────────────────────
echo "→ Creating ${ZIP_PATH}..."
cd "${BUILD_DIR}"
zip -r "${ZIP_PATH}" . \
  -x "*.pyc" \
  -x "*__pycache__*" \
  -x "*.dist-info/RECORD" \
  -x "deployment.zip" \
  > /dev/null

# ── 5. Verify the bundle contains the critical modules ──────────────
# Guards against the mcp 2.0.0 breakage (mcp.server.fastmcp removed) and any
# stale/incomplete vendoring. Also confirms EVERY first-party src module made
# it into the bundle root (server.py imports them by bare name at runtime).
# Uses an inline Python heredoc reading namelist() directly — `unzip -l` and
# `python3 -m zipfile -l` are unreliable on macOS.
echo "→ Verifying bundle contents..."
FIRST_PARTY="$(cd "${REPO_ROOT}/src" && ls *.py | tr '\n' ' ')"
python3 - "${ZIP_PATH}" "${FIRST_PARTY}" <<'PY'
import zipfile, sys
names = zipfile.ZipFile(sys.argv[1]).namelist()
first_party = sys.argv[2].split()
required = ["mcp/server/fastmcp/", "boto3/", "botocore/"] + first_party
fail = False
for pat in required:
    # First-party modules must be at the bundle ROOT (exact name match).
    if pat.endswith(".py"):
        ok = pat in names
    else:
        ok = any(pat in n for n in names)
    print(("    [ok]   " if ok else "    [FAIL] ") + pat)
    fail = fail or not ok
# Explicitly reject the broken mcp 2.x layout (no fastmcp compat layer)
if not any("mcp/server/fastmcp/" in n for n in names):
    print("    !!! mcp.server.fastmcp missing — mcp 2.x likely resolved. Pin mcp<2.0.0.")
    fail = True
if fail:
    print("!!! Bundle INCOMPLETE — do not deploy."); sys.exit(1)
print(f"    [ok]   {len(names)} entries verified")
PY

echo "════════════════════════════════════════════════════════"
echo "  ✓ Build complete: ${ZIP_PATH}"
echo "  Size: $(du -h "${ZIP_PATH}" | cut -f1)"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Next: scripts/deploy.sh uploads build/deployment.zip to your S3 artifact bucket."
