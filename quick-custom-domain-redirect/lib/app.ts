import { App } from 'aws-cdk-lib';
import { ConfigParser, createStackName } from './common/config';
import { QuickRedirectStack } from './stacks/quick-redirect-stack';

const app = new App();

const config = new ConfigParser().parse((key: string) => app.node.tryGetContext(key));

const stackName = createStackName(config.projectName, 'quickRedirect');

new QuickRedirectStack(app, stackName, {
  config,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-1',
  },
});
