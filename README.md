<div align="center">
  <div>
    <a href="https://aws.amazon.com/quick/">
      <img width="150" height="150" alt="Amazon Quick" src="static/images/amazonquick.png" />
   </a>
  </div>

<h1>
      Amazon Quick Knowledge Hub
  </h1>

<div align="center">
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/commit-activity"><img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues"><img alt="GitHub open issues" src="https://img.shields.io/github/issues/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/pulls"><img alt="GitHub open pull requests" src="https://img.shields.io/github/issues-pr/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
  </div>
</div>

## What is this?

This is the knowledge hub for [Amazon Quick](https://aws.amazon.com/quick/): working, deployable code and step-by-step guides for connecting your data, building agents and MCP servers, embedding Quick in your apps, and running it in production. Maintained by the Amazon Quick team as a companion to the [official documentation](https://docs.aws.amazon.com/quick/latest/userguide/).

## Explore by what you want to do

|     | Section                                                                                                            | Use it to                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| 🔌  | [Integration](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/integration/)                  | Bring third-party services into Quick as knowledge sources and action connectors, or deploy a ready-made MCP server |
| 🏗️  | [Infrastructure](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/infrastructure/)            | Stand up and operate Quick — account bootstrap, custom sign-in domains, observability, and row-level data security  |
| 🚀  | [Use cases](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/examples/)                       | Ship a full solution end to end — embedding, document generation, compliance automation, and more                   |
| 🖥️  | [Quick on desktop](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/amazon-quick-on-desktop/) | Wire up Amazon Cognito as an OIDC provider for the desktop app                                                      |

## Local development

The site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and managed with `uv`. To run it locally:

```bash
pip install uv
uv sync --dev
uv run mkdocs serve
```

The site is available at `http://127.0.0.1:8000`. Changes to files in `docs/` are reflected immediately.

## Contributing

See [How to Contribute](docs/HOW-TO-CONTRIBUTE.md) for the full guide. Fork the repo, add your project under the relevant top-level section folder (`infrastructure/`, `integration/`, `examples/`, or `amazon-quick-on-desktop/`) with a `README.md` as its landing page, run `make build` to verify, and open a PR. The documentation site (under `docs/`) picks up each project's `README.md` automatically.

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

## Contributors

<a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=aws-samples/sample-amazon-quick-suite-knowledge-hub" />
</a>
