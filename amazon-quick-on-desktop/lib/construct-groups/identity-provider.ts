import { RemovalPolicy, Stack } from 'aws-cdk-lib';
import { UserPool, UserPoolClient, Mfa, OAuthScope } from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';
import {
  CognitoDomainPrefix,
  ProjectName,
  ResourceName,
  createConstructId,
  createDomainPrefix,
  createResourceName,
} from '../common/config';

export interface IdentityProviderProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
  readonly mfaRequired?: boolean;
}

export class IdentityProvider extends Construct {
  public readonly pool: UserPool;
  public readonly client: UserPoolClient;
  public readonly issuerUrl: string;
  public readonly cognitoDomain: string;

  constructor(scope: Construct, id: string, props: IdentityProviderProps) {
    super(scope, id);

    const { projectName, removalPolicy, mfaRequired } = props;
    const { account, region } = Stack.of(this);
    const domainPrefix = createDomainPrefix(CognitoDomainPrefix.DEFAULT, account, region);

    this.pool = new UserPool(this, createConstructId('Pool'), {
      userPoolName: createResourceName(projectName, ResourceName.USER_POOL),
      selfSignUpEnabled: false,
      signInAliases: { username: true, email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      mfa: mfaRequired ? Mfa.REQUIRED : Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      removalPolicy,
    });

    this.pool.addDomain('Domain', {
      cognitoDomain: { domainPrefix },
    });

    this.client = this.pool.addClient('Client', {
      userPoolClientName: createResourceName(projectName, ResourceName.APP_CLIENT),
      generateSecret: false,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [OAuthScope.OPENID, OAuthScope.EMAIL, OAuthScope.PROFILE],
        callbackUrls: ['http://localhost:18080'],
      },
      authFlows: { userSrp: true },
    });

    this.issuerUrl = `https://cognito-idp.${region}.amazonaws.com/${this.pool.userPoolId}`;
    this.cognitoDomain = `https://${domainPrefix}.auth.${region}.amazoncognito.com`;
  }
}
