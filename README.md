<div align="center">
  <div>
    <a href="https://aws.amazon.com/quicksuite/">
      <img width="150" height="150" alt="Amazon Quick Suite" src="static/images/quicksuite.png" />
   </a>
  </div>

  <h1>
      Amazon Quick Suite Knowledge Hub
  </h1>

  <h2>
    Integration guides and documentation for Amazon Quick Suite
  </h2>

  You can read directly on github [https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/)

  <div align="center">
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/commit-activity"><img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/issues"><img alt="GitHub open issues" src="https://img.shields.io/github/issues/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/pulls"><img alt="GitHub open pull requests" src="https://img.shields.io/github/issues-pr/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
    <a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/aws-samples/sample-amazon-quick-suite-knowledge-hub"/></a>
  </div>

  <p>
    <a href="https://docs.aws.amazon.com/quicksuite/">Documentation</a>
  </p>
</div>

Welcome to the Amazon Quick Suite Knowledge Hub repository!

This repository provides comprehensive setup guides and integration documentation for Amazon Quick Suite, helping you connect various third-party services and knowledge bases to enhance your Quick Suite experience.

##  Repository Structure

###  [`Integration/`](./docs/integration/)

**Knowledge Bases and Actions**

This folder contains detailed setup guides for integrating knowledge bases and third-party actions with Quick Suite.

The structure is divided by integration type:

* **Knowledge Base**: Setup guides for integrating knowledge bases with Quick Suite
  * Confluence Cloud

* **Actions**: Setup guides for integrating third-party actions with Quick Suite
  * MCP (Model Context Protocol)
    * Bedrock KB Retrieval MCP
    * Redshift Data Query MCP
  * ServiceNow (2LO)
  * Asana
  * Box
  * Confluence Cloud
  * Jira Cloud
  * MS Outlook
  * MS SharePoint
  * MS Teams
  * PagerDuty
  * Salesforce
  * ServiceNow
  * Slack
  * Smartsheet

###  [`Guidance/`](./docs/guidance/)

**How-to Guides**

Step-by-step guides and best practices for working with Quick Suite.

* **[Quick Suite Bootstrap](./docs/guidance/quick-suite-bootstrap/)**: Terraform module to set up Amazon Quick Suite with IAM Identity Center integration

###  [`Use Cases/`](./docs/use-cases/)

**Real-world Implementation Examples**

Complete end-to-end solutions demonstrating Quick Suite integrations:

* **[Actuarial Analysis Solution](./docs/use-cases/actuarial-analysis-solution/)**: Comprehensive actuarial analysis workflow
* **[Bedrock KB Retrieval MCP](./docs/use-cases/bedrock-kb-retrieval-mcp/)**: Amazon Bedrock Knowledge Base integration with MCP Actions
* **[Redshift Data Query MCP](./docs/use-cases/redshift-data-query-mcp/)**: Amazon Redshift database operations through MCP integration

##  Contributing

Want to add your own integration guide or use case? See our [How to Contribute](docs/HOW-TO-CONTRIBUTE.md) guide for step-by-step instructions on adding your project to this documentation site.

##  License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contributors

<a href="https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=aws-samples/sample-amazon-quick-suite-knowledge-hub" />
</a>
