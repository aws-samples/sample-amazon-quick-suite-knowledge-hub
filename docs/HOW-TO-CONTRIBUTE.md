# How to Contribute

Welcome to the Amazon Quick Suite Knowledge Hub! We're excited to have you contribute to our growing collection of integration guides, use cases, and documentation.

This repository provides comprehensive setup guides and integration documentation for Amazon Quick Suite, helping you connect various third-party services and knowledge bases to enhance your Quick Suite experience.

## How You Can Help

We welcome contributions from our community! Here's how you can help improve the Knowledge Hub:

### 📚 Add Guidance Documentation

Share your expertise on Amazon Quick Suite best practices and administration:

- **Governance**: Access control, user management, organizational policies
- **Infrastructure as Code (IaC)**: CloudFormation, CDK, Terraform templates for Quick Suite deployments
- **Administration**: Configuration management, monitoring, troubleshooting guides
- **Best Practices**: Security guidelines, operational procedures, compliance frameworks

### 📝 Add Integration Guides

Have experience integrating a service with Amazon Quick Suite? Share your knowledge:

- Step-by-step setup instructions
- Configuration examples and screenshots
- Troubleshooting common issues
- Include screenshots and code examples

### 🎯 Share Use Cases

Built something cool with Amazon Quick Suite? We'd love to feature it:

- Complete end-to-end solutions
- Real-world implementation examples
- Architecture diagrams and explanations
- Performance optimization tips

---

## 🐛 Reporting Bugs

Found something that doesn't work as expected? Please [open a bug report](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=bug_report.md) with:

- A clear description of the problem
- Steps to reproduce the issue
- Expected vs actual behavior
- Environment details and error messages

## 📚 Improve Documentation

Spot an error or unclear explanation in our docs? Please [open a documentation improvement](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=documentation.md) with:

- Link to the documentation page
- Description of the issue or improvement
- Suggested changes

## 🙋 Get Help

Need help with setup, troubleshooting, or missing integration guides? Please [ask for help](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=help_needed.md) with:

- Brief summary of what you need help with
- Details about what you've tried
- Environment information and error messages

## 🎯 Share Examples

Created something cool with Amazon Quick Suite? We'd love to hear about your use cases:

- Open a "Show and Tell" discussion in our [Discussions forum](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/discussions)
- Share your experience and learnings
- Help other users with questions

## 🚀 Contributing via Pull Requests

Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

- You are working against the latest source on the **main** branch
- You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed the problem already
- You open an issue to discuss any significant work - we would hate for your time to be wasted

To send us a pull request, please:

1. **Fork the repository**
2. **Modify the source** - please focus on the specific change you are contributing. If you also reformat all the code, it will be hard for us to focus on your change
3. **Ensure local tests pass** (run `mkdocs serve` to verify your changes work locally)
4. **Update mkdocs.yml navigation** if adding new content to make it visible in the site menu
5. **Commit to your fork** using clear commit messages
6. **Send us a pull request**, answering any default questions in the pull request interface
7. **Pay attention to any automated CI failures** reported in the pull request, and stay involved in the conversation

GitHub provides additional documentation on [forking a repository](https://help.github.com/articles/fork-a-repo/) and [creating a pull request](https://help.github.com/articles/creating-a-pull-request/).

## Contributing Guidelines

### Before You Start

1. **Search first**: Check if a similar issue or request already exists
2. **Open an issue**: Discuss significant changes before starting work
3. **Follow conventions**: Use our naming and structure guidelines
4. **Stay focused**: Keep discussions on topic

### Content Standards

When contributing content, please ensure:

- **Accuracy**: All information is correct and up-to-date
- **Clarity**: Instructions are clear and easy to follow
- **Completeness**: Include all necessary steps and requirements
- **Consistency**: Follow the existing documentation style and format

### File Organization

- **Guidance documentation**: Place in `docs/guidance/`
- **Integration guides**: Place in `docs/integration/knowledge-base/` or `docs/integration/actions/`
- **Use cases**: Place in `docs/use-cases/`
- **Images**: Store in appropriate `images/` subdirectories

### Content Format

- Use **Markdown** for all documentation
- Include a **README.md** file for each integration or use case
- Add **frontmatter** with metadata when applicable:

  ```yaml
  ---
  category: Capability
  description: "Brief description of your project for metadata"
  ---
  ```

## Issue Guidelines

When creating an issue:

- **Use clear titles**: Be specific about the problem or request
- **Provide context**: Include relevant background information
- **Add labels**: Help categorize your issue appropriately
- **Follow templates**: Use our issue templates when available
- **Be respectful**: Follow our Code of Conduct

## Security Issues

For security vulnerabilities, please **DO NOT** open a public issue. Instead:

- Email us directly at the security contact listed in our repository
- Provide detailed information about the vulnerability
- Allow time for us to address the issue before public disclosure
- See our [Security Policy](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/security/policy) for more details

## Questions and Support

- **Documentation questions**: Use [GitHub Discussions](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/discussions)
- **Technical support**: Contact AWS Support for Quick Suite issues
- **Community support**: Join the [Amazon Quick Suite Community](https://community.amazonquicksight.com/)

## Development Setup

### About Package Management

This project uses `uv` for modern Python dependency management with faster installs and reproducible builds.

The repository includes:

- `pyproject.toml` - Project metadata and dependencies
- `uv.lock` - Locked dependency versions for reproducibility

### Prerequisites

- Python 3.8 or higher

### Local Development

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR-USERNAME/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub

# Install uv and sync dependencies
pip install uv
uv sync --dev

# Install pre-commit hooks (one-time)
uv run pre-commit install
```

That's it! You're ready to develop.

### Serve Documentation Locally

```bash
# Serve documentation locally
uv run mkdocs serve

# View your changes at http://127.0.0.1:8000
```

### Daily Development Workflow

Pre-commit hooks will now run automatically:

```bash
# Make your changes (edit files in your editor)
# For example: add a new integration guide or use case

# Commit (hooks run automatically)
git commit -m "docs: add new integration guide"
# ↑ All quality checks run here (~30-60 seconds)

# Push (documentation build runs automatically)
git push origin my-branch
# ↑ GitHub Actions workflows run here (~2-5 minutes)
```

### What the Hooks Check

**On every commit** (~30-60 seconds):
✅ **Code formatting** (auto-fixes with ruff)
✅ **Import sorting** (auto-fixes)
✅ **Linting** (with ruff)
✅ **File hygiene** (trailing whitespace, etc.)
✅ **Markdown formatting** (auto-fixes)
✅ **Security scanning** (bandit)
✅ **Documentation build** (mkdocs build --strict)

### Skipping Hooks (WIP Commits)

For work-in-progress commits, you can skip checks:

```bash
git commit --no-verify -m "wip: incomplete work"
```

**Please run all checks before opening a PR!**

### Running Checks Manually

```bash
# Run all pre-commit checks
uv run pre-commit run --all-files

# Run individual tools
uv run ruff format .
uv run ruff check --fix .
uv run bandit -r .

# Build and serve documentation
uv run mkdocs build --strict
uv run mkdocs serve

# Add new dependencies
uv add requests

# Add development dependencies
uv add --dev pytest
```

### Building for Production

To build the static site:

```bash
mkdocs build
```

The built site will be in the `site/` directory.

## Repository Structure & Conventions

### Naming Conventions

- **Files and folders**: Use lowercase, substitute dashes for spaces
- **Projects**: Avoid brand names in project/folder names

### Directory Structure

Place your project in the appropriate directory:

```
docs/
├── integration/           # For integration guides
│   ├── knowledge-base/   # Knowledge base integrations
│   └── actions/          # Action integrations
├── use-cases/            # For complete use case examples
└── guidance/             # For how-to guides and best practices
```

### Adding New Content

To add new content to the documentation site:

1. **Add files** to the appropriate directories following naming conventions
2. **Update mkdocs.yml navigation** to make your content visible in the left navigation menu
3. **Include proper frontmatter metadata** in your README.md

**All content requires a mkdocs.yml navigation entry to appear in the left nav.**

#### Examples

**Adding a Use Case:**

```yaml
nav:
  - 💡 Use Cases:
    - Your Project Name: use-cases/your-project-name/README.md  # Add here
```

**Adding Integration Guide:**

```yaml
nav:
  - 🔗 Integration:
    - Your Integration: integration/your-integration.md  # Add here
```

**Adding Guidance:**

```yaml
nav:
  - 📚 Guidance:
    - Your Guide: guidance/your-guide.md  # Add here
```

### Required Files

Each project must include:

- **README.md** - Main documentation with clear setup and usage instructions
- **LICENSE** - License file
- **.gitignore** - Appropriate gitignore file

### README.md Template

Use this structure for your README.md:

```markdown
---
category: Capability
description: "Brief description of your project for metadata"
---

# Your Project Name

Brief description of what your project does.

## Architecture
- Component 1: Description
- Component 2: Description

## Features
- Feature 1
- Feature 2

## Quick Start

### 1. Deploy Infrastructure
```bash
cd infrastructure
./deploy.sh
```

### 2. Configure Frontend

```bash
cd frontend
npm install
npm run dev
```

## Configuration

Explain any configuration options.

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct). For more information see the Code of Conduct FAQ or contact <opensource-codeofconduct@amazon.com> with any additional questions or comments.

## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.

By contributing to this project, you agree that your contributions will be licensed under the Apache 2.0 License.

## Recognition

Contributors who provide valuable content will be recognized in our project's Contributors section. We appreciate all forms of contribution, from small typo fixes to comprehensive integration guides!

## 🙏 Thank You

Your contributions help make Amazon Quick Suite more accessible and easier to use for everyone. Whether you're fixing a typo, adding a new integration guide, or sharing a complete use case, every contribution matters and is greatly appreciated!
