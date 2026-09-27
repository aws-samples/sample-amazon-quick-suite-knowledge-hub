#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { QuickLegalholdMcpStack } from '../lib/quick-legalhold-mcp-stack';

const app = new cdk.App();

new QuickLegalholdMcpStack(app, 'QuickLegalholdMcpStack', {
  stackName: 'quick-legalhold-mcp',
  // Account/region are resolved from the deployer's environment
  // (CDK_DEFAULT_ACCOUNT/REGION, set from your AWS credentials). Region falls
  // back to us-east-1 if unset. No account is hardcoded.
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || process.env.AWS_REGION || 'us-east-1',
  },
  description:
    'MCP control path (AgentCore Gateway) for Amazon Quick legal-hold management. Self-contained stack.',
});

cdk.Tags.of(app).add('Project', 'quick-legal-hold-agent');
cdk.Tags.of(app).add('Env', 'test');
