#!/usr/bin/env node
const cdk = require('aws-cdk-lib');
const { FinanceDashboardStack } = require('../lib/finance-dashboard-stack');

const app = new cdk.App();
new FinanceDashboardStack(app, 'FinanceDashboardStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1'
  }
});
