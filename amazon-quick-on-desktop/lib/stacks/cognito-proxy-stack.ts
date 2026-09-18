import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { QuickDesktopConfig, createConstructId, getRemovalPolicy } from '../common/config';
import { IdentityProvider } from '../construct-groups/identity-provider';
import { AuthProxy } from '../construct-groups/auth-proxy';

export interface CognitoProxyStackProps extends StackProps {
  readonly config: QuickDesktopConfig;
}

export class CognitoProxyStack extends Stack {
  constructor(scope: Construct, id: string, props: CognitoProxyStackProps) {
    super(scope, id, props);

    const { config } = props;
    const { projectName, retainResources, allowedCidrs, mfaRequired } = config;
    const removalPolicy = getRemovalPolicy(retainResources);

    const identity = new IdentityProvider(this, createConstructId('Identity'), {
      projectName,
      removalPolicy,
      mfaRequired,
    });

    const proxy = new AuthProxy(this, createConstructId('Proxy'), {
      projectName,
      cognitoDomain: identity.cognitoDomain,
      allowedCidrs,
    });

    new CfnOutput(this, 'PoolId', { value: identity.pool.userPoolId });
    new CfnOutput(this, 'ClientId', { value: identity.client.userPoolClientId });
    new CfnOutput(this, 'IssuerUrl', { value: identity.issuerUrl });
    new CfnOutput(this, 'AuthEndpoint', { value: `${proxy.api.url}oauth2/authorize` });
    new CfnOutput(this, 'TokenEndpoint', { value: `${proxy.api.url}oauth2/token` });
    new CfnOutput(this, 'JwksUri', { value: `${identity.issuerUrl}/.well-known/jwks.json` });
  }
}
