import os
import re


def define_env(env):
    """Hook for defining macros and variables"""

    @env.macro
    def get_integrations():
        """Dynamically scan integration directories"""
        integrations = {"knowledge-base": [], "actions": []}

        integration_configs = [
            {"type": "actions", "dir": "docs/integration/actions", "key": "actions"},
            {
                "type": "knowledge base",
                "dir": "docs/integration/knowledge base",
                "key": "knowledge-base",
            },
        ]

        for config in integration_configs:
            dir_path = config["dir"]
            if os.path.exists(dir_path):
                for item in os.listdir(dir_path):
                    item_path = os.path.join(dir_path, item)
                    readme_path = os.path.join(item_path, "README.md")

                    if os.path.isdir(item_path) and os.path.exists(readme_path):
                        title = (
                            get_title_from_file(readme_path)
                            or item.replace("-", " ").title()
                        )
                        # Relative path from integration/ directory
                        relative_path = f"{config['type']}/{item}/README.md"
                        integrations[config["key"]].append(
                            {"title": title, "path": relative_path}
                        )

        return integrations

    @env.macro
    def get_use_cases():
        """Dynamically scan use-cases directory and categorize by any README tags"""

        def extract_frontmatter(content):
            """Extract metadata from YAML frontmatter"""
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                frontmatter = match.group(1)
                category_match = re.search(r"category:\s*(\w+)", frontmatter)
                description_match = re.search(
                    r'description:\s*["\']([^"\']+)["\']', frontmatter
                )

                return {
                    "category": category_match.group(1) if category_match else None,
                    "description": description_match.group(1)
                    if description_match
                    else None,
                }
            return {}

        categories = {}

        # Scan use-cases directory
        use_cases_dir = "docs/use-cases"
        if os.path.exists(use_cases_dir):
            for item in os.listdir(use_cases_dir):
                item_path = os.path.join(use_cases_dir, item)
                readme_path = os.path.join(item_path, "README.md")

                # Skip files and directories without README.md
                if not os.path.isdir(item_path) or not os.path.exists(readme_path):
                    continue

                # Read README and extract metadata
                with open(readme_path, encoding="utf-8") as f:
                    content = f.read()
                    meta = extract_frontmatter(content)

                    # Extract title from first # heading if available
                    title = (
                        get_title_from_file(readme_path)
                        or item.replace("-", " ").title()
                    )

                    project_info = {
                        "title": title,
                        "url": f"{item}/README.md",
                        "description": meta.get("description", f"{title} solution"),
                    }

                    # Dynamically create categories
                    category = meta.get("category")
                    if category:
                        if category not in categories:
                            categories[category] = []
                        categories[category].append(project_info)

        return categories


def get_title_from_file(file_path):
    """Extract title from markdown file's first heading"""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            return title_match.group(1) if title_match else None
    except Exception:
        return None
