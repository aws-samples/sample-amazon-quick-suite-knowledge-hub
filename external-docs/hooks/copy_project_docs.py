"""
MkDocs build hook: publish documentation from code projects that live at the
repository root (outside ``external-docs/``).

Code projects were moved to the repository root so the repo is browsable without
wading through source. Their documentation (project ``README.md`` + any
``docs/*.md`` and nested markdown folders + ``images/``) is copied at build time
into ``external-docs/docs/`` at a clean section path, so pages render at clean
URLs like ``/infrastructure-as-code/quick-resource-migrator-mcp/``.

Only the files copied here are published — project source code never leaks onto
the site. Staged directories are removed on shutdown so the working tree stays
clean.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# repo root = two levels up from external-docs/hooks/
ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "external-docs" / "docs"

# Map: root project directory  ->  clean site path under docs/.
# The project README.md becomes <path>/index.md; docs/*.md keep their docs/
# prefix so in-README links like `docs/foo.md` resolve; images/ are copied.
PROJECTS: dict[str, str] = {
    # Infrastructure as Code
    "Terraform": "infrastructure-as-code/terraform",
    "quick-custom-domain-redirect": "infrastructure-as-code/quick-custom-domain-redirect",
    "quick-migrator-mcp-server": "infrastructure-as-code/quick-resource-migrator-mcp",
    # Manage Quick
    "observability-agent": "manage-quick/observability",
    "manage-quick-security/rls-dataset-shaping": "manage-quick/security/rls-dataset-shaping",
    # Amazon Quick on desktop
    "amazon-quick-on-desktop": "amazon-quick-on-desktop",
    # Use cases
    "actuarial-analysis-solution": "use-cases/actuarial-analysis-solution",
    "compliance-assistant-mcp": "use-cases/compliance-assistant-mcp",
    "document-generation-mcp-agentcore-runtime": "use-cases/document-generation-mcp-agentcore-runtime",
    "finance-dashboard-embedding": "use-cases/finance-dashboard-embedding",
    "genai-operations-hub": "use-cases/genai-operations-hub",
    "quick-chat-agent-embedding-demo": "use-cases/quick-chat-agent-embedding-demo",
    "salesforce-chat-embed": "use-cases/salesforce-chat-embed",
    "sharepoint-list-to-quicksight-dataset": "use-cases/sharepoint-list-to-quicksight-dataset",
    # Integration - MCP servers
    "bedrock-kb-retrieval-mcp": "integration/mcp/bedrock-kb-retrieval-mcp",
    "custom-mcp-server-agentcore-gateway": "integration/mcp/custom-mcp-server-agentcore-gateway",
    "custom-mcp-server-agentcore-runtime": "integration/mcp/custom-mcp-server-agentcore-runtime",
    "gateway-agentcore-s3-crud-mcp": "integration/mcp/gateway-agentcore-s3-crud-mcp",
    "powerpoint-creator-mcp": "integration/mcp/powerpoint-creator-mcp",
    "redshift-data-query-mcp": "integration/mcp/redshift-data-query-mcp",
    "testing-mcp-oauth-flow": "integration/mcp/testing-mcp-oauth-flow",
}

_SKIP_MD = {"SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"}
_CODE_SUFFIXES = {".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".go", ".sh"}
_IMG_DIRS = ("images", "img", "assets", "diagrams", "screenshots")
# Extra top-level markdown files a README commonly links to (copied if present).
_EXTRA_MD = (
    "enable_ssl.md",
    "guide.md",
    "mcp_3LO_auth_flow.md",
    "EMBEDDING_SETUP.md",
)

# Track staged destinations for cleanup on shutdown.
_staged: list[Path] = []


def _copy_images(src: Path, dest: Path) -> None:
    for d in _IMG_DIRS:
        p = src / d
        if p.is_dir():
            shutil.copytree(p, dest / d, dirs_exist_ok=True)


def _copy_project(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    # 1. README.md -> index.md (landing page)
    readme = src / "README.md"
    if readme.is_file():
        shutil.copy2(readme, dest / "index.md")

    # 2. docs/ subfolder — keep the docs/ prefix so README links resolve
    proj_docs = src / "docs"
    if proj_docs.is_dir():
        (dest / "docs").mkdir(parents=True, exist_ok=True)
        for md in sorted(proj_docs.glob("*.md")):
            if md.name not in _SKIP_MD:
                shutil.copy2(md, dest / "docs" / md.name)
        _copy_images(proj_docs, dest / "docs")

    # 3. Nested markdown-only subfolders (e.g. agents/) preserved as-is
    for sub in sorted(p for p in src.iterdir() if p.is_dir()):
        if sub.name in {"docs", "node_modules", *_IMG_DIRS}:
            continue
        files = [f for f in sub.rglob("*") if f.is_file()]
        md_files = [f for f in files if f.suffix == ".md"]
        if md_files and not any(f.suffix in _CODE_SUFFIXES for f in files):
            shutil.copytree(sub, dest / sub.name, dirs_exist_ok=True)

    # 4. Top-level images referenced by the README
    _copy_images(src, dest)

    # 5. Extra top-level markdown files a README links to
    for name in _EXTRA_MD:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dest / name)


def on_startup(**kwargs) -> None:
    for proj, url in PROJECTS.items():
        src = ROOT / proj
        dest = DOCS / url
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            _copy_project(src, dest)
            _staged.append(dest)


def on_shutdown(**kwargs) -> None:
    for dest in _staged:
        if dest.exists():
            shutil.rmtree(dest)
    _staged.clear()
