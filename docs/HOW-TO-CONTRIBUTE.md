# How to Contribute

Thanks for contributing to the Amazon Quick Knowledge Hub. This guide covers the
essentials: run the docs site locally, add your content, and open a pull request.

## 1. Run the site locally

You need Python 3.9+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# Fork the repo on GitHub, then clone your fork
git clone https://github.com/YOUR-USERNAME/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub

# Install dependencies
pip install uv
uv sync --dev

# Start the live-preview server
make serve
```

Open <http://127.0.0.1:8000>. The site reloads automatically as you edit files.

!!! tip "Common commands"
| Command | What it does |
| \------------------ | ------------------------------------------------- |
| `make serve` | Live preview at `http://127.0.0.1:8000` |
| `make build` | Build the static site into `site/` |
| `make build-strict`| Build and fail on any warning (broken links, etc.)|

## 2. Add your content

Every project lives in a top-level section folder and just needs a `README.md`
as its landing page. The site **auto-discovers** any folder with a `README.md`
and adds it to the left navigation — there is no `mkdocs.yml` nav to edit.

| Section folder             | Use it for                                    |
| -------------------------- | --------------------------------------------- |
| `integration/`             | Connecting third-party services, MCP servers  |
| `infrastructure/`          | Terraform / CDK templates, observability, RLS |
| `examples/`                | Complete, deployable end-to-end use cases     |
| `amazon-quick-on-desktop/` | Desktop app setup                             |

To add a project:

1. Create a folder under the right section, e.g. `examples/my-cool-demo/`.
1. Add a `README.md` — this becomes the project's page.
1. Put images in an `images/` subfolder and reference them relatively
   (e.g. `![Diagram](images/diagram.png)`).
1. Run `make build-strict` to confirm there are no broken links or warnings.

!!! note
Source code, and binary files (`.zip`, `.pdf`, `.xlsx`, etc.) are **not**
published to the site. If your `README.md` needs to link to them, use a full
GitHub URL (e.g. `https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/...`).

## 3. Open a pull request

```bash
git checkout -b my-change
# make your edits, then verify locally
make build-strict

git commit -m "docs: add my cool demo"
git push -u origin my-change
```

Then open a pull request against `main`. Please:

- Keep each PR focused on a single change.
- Run `make build-strict` before pushing — CI runs the same build.
- Respond to any automated CI feedback on the PR.

## Reporting issues

- **Bug**: [open a bug report](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=bug_report.md)
- **Docs improvement**: [open a documentation issue](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=documentation.md)
- **Need help**: [ask for help](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues/new?template=help_needed.md)
- **Security vulnerability**: do **not** open a public issue — see the
  [Security Policy](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/security/policy).

## Licensing

By contributing, you agree that your contributions are licensed under the MIT-0
License. See the [LICENSE](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE) file.
