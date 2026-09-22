"""
MkDocs build hook — dynamic project documentation publishing.

Code projects live at the repository root, grouped under high-level section
folders (infrastructure/, examples/, integration/, and
the standalone amazon-quick-on-desktop/). This hook, at build time:

  1. AUTO-DISCOVERS every project (any folder containing a README.md) under the
     configured section roots — so adding a new project folder makes it appear
     on the site with no config changes.
  2. STAGES each project's README.md (-> index.md), its docs/ subfolder, any
     nested markdown-only folders, and images into docs/<section>/
     at a clean URL path. Project source code never leaks onto the site.
  3. GENERATES the left-nav dynamically from what was discovered, grouped by
     section, and injects it into the MkDocs config.
  4. GENERATES the home-page gallery (stat counts, category cards, and
     featured solution cards) from the SAME discovery pass and injects it into
     the home page AT RENDER TIME (in memory, via on_page_markdown). The source
     docs/index.md keeps its placeholder tokens and is never modified on disk,
     so the generated counts/cards can never go stale or be committed. Add a
     project folder and the home page updates on the next build automatically.

Staged directories are removed on shutdown so the working tree stays clean.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "docs"

# --- Discovery configuration -------------------------------------------------
# Each entry: (Nav section title, filesystem root, url prefix under docs/).
# Every immediate subfolder with a README.md becomes a published project.
SECTIONS = [
    ("Infrastructure", "infrastructure", "infrastructure"),
    ("Examples", "examples", "examples"),
]
# Integration has its own nested shape (MCP servers, action setup guides, KB
# guides); handled explicitly so each group nests correctly in the nav.
INTEGRATION = {
    "root": "integration",
    "url": "integration",
    "groups": [
        # (Nav subgroup, filesystem subpath, url subpath)
        ("MCP", "actions/MCP", "mcp"),
        ("Actions", "actions", "actions"),
        ("Knowledge Base", "knowledge-base", "knowledge-base"),
    ],
}
# Standalone single-project sections (project folder == section).
STANDALONE = [
    ("Amazon Quick on desktop", "amazon-quick-on-desktop", "amazon-quick-on-desktop"),
]
# Folders to ignore during discovery (grouping shells, not projects themselves).
_IGNORE_DIRS = {"MCP", "images", "img", "assets", "node_modules", "docs"}

_SKIP_MD = {"SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"}
_CODE_SUFFIXES = {".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".go", ".sh"}
_IMG_DIRS = ("images", "img", "assets", "diagrams", "screenshots")
_EXTRA_MD = ("enable_ssl.md", "guide.md", "mcp_3LO_auth_flow.md", "EMBEDDING_SETUP.md")

_staged: list[Path] = []

# Optional friendly nav labels for folders whose auto-title reads awkwardly
# (acronyms etc.). Anything not listed is auto-titled from its folder name.
_LABEL_OVERRIDES = {
    "multi-tenant-assets-deployment": "Quick Agent Assets Deployment",
    "observability-agent": "Observability",
    "compliance-assistant-mcp": "Compliance Assistant MCP",
    "genai-operations-hub": "GenAI Operations Hub",
    "bedrock-kb-retrieval-mcp": "Amazon Bedrock KB Retrieval MCP",
    "redshift-data-query-mcp": "Amazon Redshift Data Query MCP",
    "gateway-agentcore-s3-crud-mcp": "Amazon Gateway AgentCore S3 MCP",
    "custom-mcp-server-agentcore-runtime": "Build Custom MCP Server (AgentCore Runtime)",
    "custom-mcp-server-agentcore-gateway": "Build Custom MCP Server (AgentCore Gateway)",
    "powerpoint-creator-mcp": "PowerPoint Creator MCP",
    "testing-mcp-oauth-flow": "Testing MCP OAuth Flow",
    "sharepoint-list-to-quicksight-dataset": "Export SharePoint Lists to Quick Sight",
    "salesforce-chat-embed": "Embed Quick Chat Agent in Salesforce",
    "quick-chat-agent-embedding-demo": "Quick Chat Agent Embedding Demo",
    "document-generation-mcp-agentcore-runtime": "Document Generation MCP (AgentCore Runtime)",
    "quick-custom-domain-redirect": "Quick Custom Domain Redirect",
    "rls-dataset-shaping": "Row-Level Security & Dataset Shaping",
    "2lo-servicenow-action-setup-guide": "ServiceNow (2LO)",
    "confluence-cloud-knowledge-only-setup-guide": "Confluence Cloud",
}

# Common acronyms to upper-case when auto-titling.
_ACRONYMS = {
    "Mcp": "MCP",
    "Oidc": "OIDC",
    "Iam": "IAM",
    "S3": "S3",
    "Kb": "KB",
    "Oauth": "OAuth",
    "Rls": "RLS",
    "Ai": "AI",
    "Genai": "GenAI",
    "Api": "API",
    "Sdk": "SDK",
    "Cdk": "CDK",
}


def _title(slug: str) -> str:
    """Human nav label from a folder slug, honoring overrides + acronyms."""
    if slug in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[slug]
    words = slug.replace("-", " ").replace("_", " ").title().split()
    return " ".join(_ACRONYMS.get(w, w) for w in words)


# ── Home-page gallery generation ────────────────────────────────────────────
# Everything below drives the landing page. It is populated from the same
# discovery pass that builds the nav, so counts and cards never go stale.
#
# _CARDS collects one record per discovered project:
#   {"title", "url", "desc", "tags", "group"}   (group is the gallery bucket)
_CARDS: list[dict] = []

# Number of featured cards to show on the landing page.
_FEATURED_LIMIT = 8


def _read_frontmatter(readme: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) for a README with optional YAML block."""
    text = readme.read_text(encoding="utf-8", errors="ignore")
    fm: dict = {}
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            block = stripped[3:end]
            body = stripped[end + 4 :]
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip().lower()] = v.strip().strip('"').strip("'")
    return fm, body


def _derive_desc(fm: dict, body: str) -> str:
    """One-line description: frontmatter `description` wins, else first prose line."""
    if fm.get("description"):
        d = fm["description"]
    else:
        d = ""
        for raw in body.splitlines():
            s = raw.strip()
            if not s or s.startswith(("#", "!", "|", ">", "```", "<", "---", "[![")):
                continue
            s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)  # links -> text
            s = re.sub(r"[*`_]", "", s)  # md emphasis
            if len(s) > 30:
                d = s
                break
    d = d.strip()
    if len(d) > 165:
        d = d[:162].rsplit(" ", 1)[0] + "…"
    return d


def _derive_tags(fm: dict, title: str) -> list[str]:
    """Tag pills: explicit frontmatter `tags` if present, else a light heuristic."""
    if fm.get("tags"):
        raw = re.split(r"[,;]", fm["tags"].strip("[]"))
        tags = [t.strip().strip("\"'") for t in raw if t.strip()]
        return tags[:3]
    tags: list[str] = []
    hay = f"{title} {fm.get('description', '')}".lower()
    for kw, label in (
        ("mcp", "MCP"),
        ("agentcore", "AgentCore"),
        ("embed", "Embed"),
        ("terraform", "Terraform"),
        ("dashboard", "Dashboards"),
        ("observ", "Monitoring"),
        ("security", "Security"),
        ("oauth", "OAuth"),
        ("sharepoint", "SharePoint"),
        ("compliance", "Compliance"),
        ("document", "Documents"),
    ):
        if kw in hay and label not in tags:
            tags.append(label)
    return tags[:3]


def _project_recency(proj: Path) -> float:
    """Return a 'recently added/updated' score for ordering featured cards.

    Uses the project's most recent git commit timestamp (accurate signal for
    when a project was added or last touched, independent of clone/checkout
    time). Falls back to the newest file mtime if git is unavailable (e.g.
    building from a tarball or a shallow export).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%ct", "--", str(proj)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ts = out.stdout.strip()
        if ts:
            return float(ts)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # Fallback: newest mtime among the project's files.
    try:
        return max(
            (f.stat().st_mtime for f in proj.rglob("*") if f.is_file()),
            default=0.0,
        )
    except OSError:
        return 0.0


def _register_card(proj: Path, slug: str, url: str, group: str) -> None:
    """Record a project's gallery metadata (called once per staged project)."""
    readme = proj / "README.md"
    fm, body = _read_frontmatter(readme) if readme.is_file() else ({}, "")
    _CARDS.append(
        {
            "title": _title(slug),
            "url": f"{url}/",
            "desc": _derive_desc(fm, body),
            "tags": _derive_tags(fm, _title(slug)),
            "group": group,
            "added": _project_recency(proj),
        }
    )


# Category buckets shown in the "Browse the hub" row, in display order.
# (group key used in _register_card, display name, blurb)
_CATEGORY_ORDER = [
    (
        "integration-actions",
        "Integrations",
        "Connect third-party services as action connectors and knowledge sources: "
        "Slack, Jira, Salesforce, ServiceNow, SharePoint, and more.",
    ),
    (
        "integration-mcp",
        "MCP servers",
        "Deployable Model Context Protocol servers you can connect to Quick, "
        "built on AgentCore Runtime and Gateway.",
    ),
    (
        "infrastructure",
        "Infrastructure",
        "Building blocks for running Quick: Terraform bootstrap, custom domain, "
        "observability, row-level security, and deployment.",
    ),
    (
        "examples",
        "Use cases",
        "Complete, deployable end-to-end solutions covering embedding, document "
        "generation, compliance, and operational dashboards.",
    ),
    (
        "standalone",
        "Desktop",
        "Deploy Amazon Cognito as an OIDC provider for the Amazon Quick desktop "
        "app in enterprise environments.",
    ),
]


def _first_url_for_group(group: str) -> str:
    for c in _CARDS:
        if c["group"] == group:
            return c["url"]
    return "."


def _render_stats() -> str:
    total = len(_CARDS)
    integrations = sum(
        1 for c in _CARDS if c["group"] in ("integration-actions", "integration-mcp")
    )
    mcp = sum(1 for c in _CARDS if c["group"] == "integration-mcp")
    use_cases = sum(1 for c in _CARDS if c["group"] == "examples")
    pairs = [
        (total, "solutions"),
        (integrations, "integrations"),
        (mcp, "MCP servers"),
        (use_cases, "use cases"),
    ]
    return "".join(f"<span><strong>{n}</strong> {label}</span>\n" for n, label in pairs)


def _render_categories() -> str:
    out = []
    for group, name, blurb in _CATEGORY_ORDER:
        count = sum(1 for c in _CARDS if c["group"] == group)
        if not count:
            continue
        url = _first_url_for_group(group)
        out.append(
            f'<a class="qh-cat" href="{url}">'
            f'<span class="qh-cat__count">{count}</span>'
            f'<span class="qh-cat__name">{name}</span>'
            f'<span class="qh-cat__desc">{blurb}</span>'
            f'<span class="qh-cat__go">Explore →</span></a>'
        )
    return "\n".join(out)


def _render_featured() -> str:
    # Show the most recently added/updated solutions first, so new projects
    # surface at the top of the grid automatically — no manual curation.
    ordered = sorted(_CARDS, key=lambda c: c.get("added", 0.0), reverse=True)
    out = []
    for c in ordered[:_FEATURED_LIMIT]:
        tags = "".join(f"<span>{t}</span>" for t in c["tags"])
        tag_html = f'<span class="qh-card__tags">{tags}</span>' if tags else ""
        desc = c["desc"] or ""
        out.append(
            f'<a class="qh-card" href="{c["url"]}">'
            f'<span class="qh-card__title">{c["title"]}</span>'
            f'<span class="qh-card__desc">{desc}</span>'
            f"{tag_html}</a>"
        )
    return "\n".join(out)


def _render_home(markdown: str) -> str:
    """Return the home markdown with gallery tokens replaced by generated HTML.

    Pure in-memory transform — the source index.md on disk is never modified,
    so the working tree always keeps the token version and the generated
    counts/cards can never be accidentally committed or go stale.
    """
    replacements = {
        "<!-- QH:STATS -->": _render_stats(),
        "<!-- QH:CATEGORIES -->": _render_categories(),
        "<!-- QH:FEATURED -->": _render_featured(),
    }
    for token, html in replacements.items():
        markdown = markdown.replace(token, html)
    return markdown


def _copy_images(src: Path, dest: Path) -> None:
    for d in _IMG_DIRS:
        p = src / d
        if p.is_dir():
            shutil.copytree(p, dest / d, dirs_exist_ok=True)


def _copy_project(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    readme = src / "README.md"
    if readme.is_file():
        shutil.copy2(readme, dest / "index.md")
    proj_docs = src / "docs"
    if proj_docs.is_dir():
        (dest / "docs").mkdir(parents=True, exist_ok=True)
        for md in sorted(proj_docs.glob("*.md")):
            if md.name not in _SKIP_MD:
                shutil.copy2(md, dest / "docs" / md.name)
        _copy_images(proj_docs, dest / "docs")
    for sub in sorted(p for p in src.iterdir() if p.is_dir()):
        if sub.name in {"docs", "node_modules", *_IMG_DIRS}:
            continue
        files = [f for f in sub.rglob("*") if f.is_file()]
        md_files = [f for f in files if f.suffix == ".md"]
        if md_files and not any(f.suffix in _CODE_SUFFIXES for f in files):
            shutil.copytree(sub, dest / sub.name, dirs_exist_ok=True)
    _copy_images(src, dest)
    for name in _EXTRA_MD:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dest / name)


def _discover_project(src: Path) -> Path | None:
    """Return the folder that actually holds the project README.

    Handles projects whose README is one level down (e.g. a section folder
    -> section/project-name/README.md).
    """
    if (src / "README.md").is_file():
        return src
    # single meaningful child with a README
    child_readmes = [
        d for d in src.iterdir() if d.is_dir() and (d / "README.md").is_file()
    ]
    if len(child_readmes) == 1:
        return child_readmes[0]
    return None


def _stage_project(child: Path, url: str, items: list, group: str = "") -> None:
    """Discover, stage, register gallery metadata, and add a nav entry."""
    proj = _discover_project(child)
    if proj is None:
        return
    _copy_project(proj, DOCS / url)
    _staged.append(DOCS / url)
    items.append({_title(child.name): f"{url}/index.md"})
    if group:
        _register_card(proj, child.name, url, group)


def _build_and_stage() -> list:
    """Discover + stage projects; return the generated nav (list structure)."""
    nav: list = [{"Home": "index.md"}]

    # --- Standalone single-project sections (placed at the top) ---
    for title, root_name, url_prefix in STANDALONE:
        src = ROOT / root_name
        proj = _discover_project(src) if src.is_dir() else None
        if proj is not None:
            _copy_project(proj, DOCS / url_prefix)
            _staged.append(DOCS / url_prefix)
            nav.append({title: f"{url_prefix}/index.md"})
            _register_card(proj, root_name, url_prefix, "standalone")

    # --- Simple sections: every subfolder with a README is a project ---
    for title, root_name, url_prefix in SECTIONS:
        root = ROOT / root_name
        if not root.is_dir():
            continue
        items: list = []
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if child.name in _IGNORE_DIRS:
                continue
            _stage_project(child, f"{url_prefix}/{child.name}", items, group=root_name)
        if items:
            nav.append({title: items})

    # --- Integration: nested groups (MCP / Actions / Knowledge Base) ---
    iroot = ROOT / INTEGRATION["root"]
    if iroot.is_dir():
        subgroups: list = []
        for gtitle, subpath, urlsub in INTEGRATION["groups"]:
            gdir = iroot / subpath
            if not gdir.is_dir():
                continue
            gitems: list = []
            for child in sorted(p for p in gdir.iterdir() if p.is_dir()):
                if child.name in _IGNORE_DIRS:
                    continue
                # MCP servers get their own gallery bucket; everything else
                # under integration/ is an action/KB integration.
                cgroup = "integration-mcp" if urlsub == "mcp" else "integration-actions"
                _stage_project(
                    child,
                    f"{INTEGRATION['url']}/{urlsub}/{child.name}",
                    gitems,
                    group=cgroup,
                )
            if gitems:
                subgroups.append({gtitle: gitems})
        if subgroups:
            nav.append({"Integration": subgroups})

    nav.append({"How To Contribute": "HOW-TO-CONTRIBUTE.md"})
    return nav


def on_config(config, **kwargs):
    # Purge orphaned staged section roots from any interrupted prior build.
    for url_prefix in (
        [s[2] for s in SECTIONS] + [INTEGRATION["url"]] + [s[2] for s in STANDALONE]
    ):
        stale = DOCS / url_prefix.split("/")[0]
        if stale.is_dir():
            shutil.rmtree(stale)
    _staged.clear()
    _CARDS.clear()
    config["nav"] = _build_and_stage()
    return config


def on_page_markdown(markdown, page, config, files, **kwargs):
    """Inject the generated gallery into the home page at render time.

    Done in memory (not on disk) so docs/index.md always keeps its token
    placeholders and generated content is never written to the working tree.
    """
    if page.file.src_uri == "index.md":
        return _render_home(markdown)
    return markdown


def _cleanup_staged() -> None:
    """Remove all build-staged project folders from docs/ so the source tree
    stays clean. Safe to call after the site HTML has been written.

    Removes both the per-project staged dirs and the section-root parents
    (e.g. docs/infrastructure, docs/examples, docs/integration) so no empty
    shells are left behind in the working tree.
    """
    for dest in _staged:
        if dest.exists():
            shutil.rmtree(dest)
    _staged.clear()
    # Remove the section-root directories themselves (including empty shells).
    for url_prefix in (
        [s[2] for s in SECTIONS] + [INTEGRATION["url"]] + [s[2] for s in STANDALONE]
    ):
        root = DOCS / url_prefix.split("/")[0]
        if root.is_dir():
            shutil.rmtree(root)


def on_post_build(**kwargs) -> None:
    # `mkdocs build` does not fire on_shutdown, so clean up here as well.
    # By this point the site/ HTML is already written, so removing the
    # staged source copies from docs/ is safe.
    _cleanup_staged()


def on_shutdown(**kwargs) -> None:
    _cleanup_staged()
