<!-- markdownlint-disable MD033 MD041 -->
<div align="center">
  <div>
    <a href="https://aws.amazon.com/quick/">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/static/images/aws-quick-logo-stacked-white-orange-smile.svg" />
        <img width="160" alt="AWS Quick" src="docs/static/images/aws-quick-logo-stacked-black-orange-smile.svg" />
      </picture>
   </a>
  </div>

<h1>
      AWS Quick Knowledge Hub
  </h1>

<div align="center">
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/commit-activity"><img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues"><img alt="GitHub open issues" src="https://img.shields.io/github/issues/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/pulls"><img alt="GitHub open pull requests" src="https://img.shields.io/github/issues-pr/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
  </div>
</div>

## What is this?

This is the knowledge hub for [AWS Quick](https://aws.amazon.com/quick/). It has working, deployable code and step-by-step guides for connecting your data, building agents and MCP servers, embedding Quick in your apps, and running it in production. It's maintained by the AWS Quick team as a companion to the [official documentation](https://docs.aws.amazon.com/quick/latest/userguide/).

## Explore by what you want to do

|     | Section                                                                                                            | Use it to                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| 🔌  | [Integration](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/integration/)                  | Bring third-party services into Quick as knowledge sources, action connectors and data sources (for example [Databricks Unity Catalog with 3LO](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/integration/data-sources/databricks-unity-catalog-3lo-setup-guide/)), or deploy a ready-made MCP server |
| 🏗️  | [Infrastructure](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/infrastructure/)            | Stand up and operate Quick: account bootstrap, custom sign-in domains, observability, row-level data security, and admin onboarding automation over MCP |
| 🚀  | [Use cases](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/examples/)                       | Ship a full solution end to end: embedding, document generation, compliance automation, and more                   |
| 🖥️  | [Quick on desktop](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/quick-desktop-oidc-cognito-setup/) | Optional: deploy Amazon Cognito as a dedicated OIDC provider for desktop sign-in                                   |

## Local development

The site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and managed with `uv`. To run it locally:

```bash
pip install uv
uv sync --dev
uv run mkdocs serve
```

The site is available at `http://127.0.0.1:8000`. Changes to files in `docs/` are reflected immediately.

## Contributing

See [How to Contribute](docs/HOW-TO-CONTRIBUTE.md) for the full guide. Fork the repo, add your project under the relevant top-level section folder (`infrastructure/`, `integration/`, `examples/`, or `quick-desktop-oidc-cognito-setup/`) with a `README.md` as its landing page, run `make build` to verify, and open a PR. The documentation site (under `docs/`) picks up each project's `README.md` automatically.

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

## Contributors

<a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/contributors">
  <img alt="Contributors" src="https://contrib.rocks/image?repo=aws-samples/sample-amazon-quick-suite-knowledge-hub" />
</a>
