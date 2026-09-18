#!/usr/bin/env node
import 'source-map-support/register';
import { App } from 'aws-cdk-lib';
import { CognitoProxyStack } from './stacks/cognito-proxy-stack';
import { ProjectName, QuickDesktopConfig, createStackName } from './common/config';

const region = process.env.CDK_DEFAULT_REGION || 'us-east-1';

const app = new App();

const retainResources = app.node.tryGetContext('retain') === 'true';
const allowedCidrs = app.node.tryGetContext('allowedCidrs') as string[] | undefined;
const mfaRequired = app.node.tryGetContext('mfaRequired') as boolean | undefined;

const config: QuickDesktopConfig = {
  projectName: ProjectName.QUICK_DESKTOP,
  retainResources,
  ...(allowedCidrs && { allowedCidrs }),
  ...(mfaRequired && { mfaRequired }),
};

new CognitoProxyStack(app, createStackName(config.projectName, 'CognitoProxy'), {
  config,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
});

app.synth();
