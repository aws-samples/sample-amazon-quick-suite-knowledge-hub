import { Duration } from 'aws-cdk-lib';
import { Function as LambdaFunction, Runtime, Code } from 'aws-cdk-lib/aws-lambda';
import { RestApi, LambdaIntegration } from 'aws-cdk-lib/aws-apigateway';
import { PolicyDocument, PolicyStatement, Effect, AnyPrincipal } from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { join } from 'path';
import { ProjectName, ResourceName, createConstructId, createResourceName } from '../common/config';

export interface AuthProxyProps {
  readonly projectName: ProjectName;
  readonly cognitoDomain: string;
  readonly allowedCidrs?: string[];
}

export class AuthProxy extends Construct {
  public readonly api: RestApi;

  constructor(scope: Construct, id: string, props: AuthProxyProps) {
    super(scope, id);

    const { projectName, cognitoDomain, allowedCidrs } = props;

    const fn = new LambdaFunction(this, createConstructId('Function'), {
      functionName: createResourceName(projectName, ResourceName.AUTH_PROXY_FUNCTION),
      runtime: Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: Code.fromAsset(join(__dirname, '..', '..', 'lambda')),
      timeout: Duration.seconds(10),
      environment: { COGNITO_DOMAIN: cognitoDomain },
    });

    const integration = new LambdaIntegration(fn);

    const policy = allowedCidrs ? this.createResourcePolicy(allowedCidrs) : undefined;

    this.api = new RestApi(this, createConstructId('Api'), {
      restApiName: createResourceName(projectName, ResourceName.AUTH_PROXY_API),
      deployOptions: { stageName: 'prod' },
      ...(policy && { policy }),
    });

    const oauth2 = this.api.root.addResource('oauth2');
    const authorize = oauth2.addResource('authorize');
    const token = oauth2.addResource('token');

    authorize.addMethod('GET', integration);
    token.addMethod('POST', integration);
  }

  private createResourcePolicy(allowedCidrs: string[]): PolicyDocument {
    return new PolicyDocument({
      statements: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          principals: [new AnyPrincipal()],
          actions: ['execute-api:Invoke'],
          resources: ['execute-api:/*/*/*'],
        }),
        new PolicyStatement({
          effect: Effect.DENY,
          principals: [new AnyPrincipal()],
          actions: ['execute-api:Invoke'],
          resources: ['execute-api:/*/*/*'],
          conditions: {
            NotIpAddress: { 'aws:SourceIp': allowedCidrs },
          },
        }),
      ],
    });
  }
}
