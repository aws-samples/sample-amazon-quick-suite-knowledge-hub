import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { QuickDesktopConfig, createConstructId, getRemovalPolicy } from '../common/config';
import { IdentityProvider } from '../construct-groups/identity-provider';

export interface CognitoStackProps extends StackProps {
  readonly config: QuickDesktopConfig;
}

export class CognitoStack extends Stack {
  constructor(scope: Construct, id: string, props: CognitoStackProps) {
    super(scope, id, props);

    const { config } = props;
    const { projectName, retainResources, mfaRequired } = config;
    const removalPolicy = getRemovalPolicy(retainResources);

    const identity = new IdentityProvider(this, createConstructId('Identity'), {
      projectName,
      removalPolicy,
      mfaRequired,
    });

    new CfnOutput(this, 'PoolId', { value: identity.pool.userPoolId });
    new CfnOutput(this, 'ClientId', { value: identity.client.userPoolClientId });
    new CfnOutput(this, 'IssuerUrl', { value: identity.issuerUrl });
    new CfnOutput(this, 'AuthEndpoint', { value: `${identity.cognitoDomain}/oauth2/authorize` });
    new CfnOutput(this, 'TokenEndpoint', { value: `${identity.cognitoDomain}/oauth2/token` });
    new CfnOutput(this, 'JwksUri', { value: `${identity.issuerUrl}/.well-known/jwks.json` });
  }
}
