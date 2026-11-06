# Documentation

Documentation for the Quick Admin Buddy MCP server — a Model Context Protocol
server that fronts the AWS Quick admin APIs for action connectors and knowledge
bases with twelve higher-level onboarding tools, deployed to Amazon Bedrock AgentCore Runtime behind an
AgentCore Gateway.

- **[Tool Reference](./tool-reference.md)** — each tool's inputs, success payload
  shape, and every `Result_Status` value; the connector and extension catalogs
  and their required config fields; and how credentials are handled as a
  `Secret_Reference`.
- **[Deployment Guide](./deployment-guide.md)** — step-by-step CloudFormation
  deployment: prerequisites, building and pushing the container image,
  `aws cloudformation deploy` with its parameters, the stack outputs
  (gateway endpoint + Cognito identifiers), creating the AWS Quick action
  connector, updates, and teardown.
- **[Authentication & Authorization Model](./auth-model.md)** — inbound Cognito
  authentication at the AgentCore Gateway, outbound `Agent_IAM_Identity`
  authorization scoped to the specific Quick admin actions the server needs, and the end-to-end
  request flow.

See the project overview (one level up) for the architecture, project layout,
and development setup.
