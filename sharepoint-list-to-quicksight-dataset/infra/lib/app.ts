#!/usr/bin/env node
import 'source-map-support/register';
import { App } from 'aws-cdk-lib';
import { SharePointExportStack } from './stacks/sharepoint-export-stack';
import { ProjectName, SharePointExportConfig, createStackName } from './common/config';

const app = new App();

const entraTenantId = app.node.tryGetContext('entraTenantId') as string;
const entraClientId = app.node.tryGetContext('entraClientId') as string;

if (!entraTenantId || !entraClientId) {
  throw new Error(
    'Required context: -c entraTenantId=<your-tenant-id> -c entraClientId=<your-client-id>',
  );
}

const retainResources = app.node.tryGetContext('retain') === 'true';

const config: SharePointExportConfig = {
  projectName: ProjectName.QUICK_SP_EXPORT,
  retainResources,
  enableXRay: true,
  entraTenantId,
  entraClientId,
};

const stackName = createStackName(config.projectName, 'Infrastructure');

new SharePointExportStack(app, stackName, {
  config,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
