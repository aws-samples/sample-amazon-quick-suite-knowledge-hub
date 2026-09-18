import { Construct } from 'constructs';
import { Duration, BundlingOutput } from 'aws-cdk-lib';
import { Runtime, Code, Function, Architecture, Tracing } from 'aws-cdk-lib/aws-lambda';
import { HttpApi, HttpMethod, CorsHttpMethod } from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpJwtAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import { Bucket } from 'aws-cdk-lib/aws-s3';
import { Secret } from 'aws-cdk-lib/aws-secretsmanager';
import { ProjectName, createResourceName, createConstructId } from '../common/config';

export interface ApiGatewayLambdaProps {
  readonly projectName: ProjectName;
  readonly exportBucket: Bucket;
  readonly entraClientSecret: Secret;
  readonly entraTokenUrl: string;
  readonly entraClientId: string;
  readonly entraTenantId: string;
  readonly account: string;
}

/** HTTP API with Entra JWT authorizer and Lambda backend. */
export class ApiGatewayLambda extends Construct {
  public readonly api: HttpApi;
  public readonly lambdaFunction: Function;

  constructor(scope: Construct, id: string, props: ApiGatewayLambdaProps) {
    super(scope, id);

    const {
      projectName,
      exportBucket,
      entraClientSecret,
      entraTokenUrl,
      entraClientId,
      entraTenantId,
      account,
    } = props;

    this.lambdaFunction = new Function(this, createConstructId('Function'), {
      functionName: createResourceName(projectName, 'ApiHandler'),
      runtime: Runtime.PYTHON_3_12,
      handler: 'api_handler.handler',
      code: Code.fromAsset('../backend/src', {
        bundling: {
          image: Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -r . /asset-output/',
          ],
          outputType: BundlingOutput.NOT_ARCHIVED,
        },
      }),
      architecture: Architecture.ARM_64,
      memorySize: 512,
      timeout: Duration.seconds(60),
      tracing: Tracing.ACTIVE,
      environment: {
        EXPORT_BUCKET: exportBucket.bucketName,
        ENTRA_TOKEN_URL: entraTokenUrl,
        ENTRA_CLIENT_ID: entraClientId,
        ENTRA_CLIENT_SECRET_ARN: entraClientSecret.secretArn,
        SERVICE: 'quick-sp-export',
        POWERTOOLS_METRICS_NAMESPACE: 'quick-sp-export',
        LOG_LEVEL: 'INFO',
        AWS_ACCOUNT_ID: account,
      },
    });

    exportBucket.grantReadWrite(this.lambdaFunction);
    entraClientSecret.grantRead(this.lambdaFunction);

    const jwtAuthorizer = new HttpJwtAuthorizer(
      createConstructId('EntraAuthorizer'),
      `https://login.microsoftonline.com/${entraTenantId}/v2.0`,
      { jwtAudience: [entraClientId] },
    );

    this.api = new HttpApi(this, createConstructId('Api'), {
      apiName: createResourceName(projectName, 'Api'),
      description: 'SharePoint List Export API',
      corsPreflight: {
        allowMethods: [CorsHttpMethod.GET, CorsHttpMethod.POST],
        allowOrigins: ['*'],
        allowHeaders: ['Authorization', 'Content-Type'],
      },
    });

    const integration = new HttpLambdaIntegration(
      createConstructId('LambdaIntegration'),
      this.lambdaFunction,
    );

    this.api.addRoutes({
      path: '/sites',
      methods: [HttpMethod.GET],
      integration,
      authorizer: jwtAuthorizer,
    });

    this.api.addRoutes({
      path: '/sites/{site_id}/lists',
      methods: [HttpMethod.GET],
      integration,
      authorizer: jwtAuthorizer,
    });

    this.api.addRoutes({
      path: '/sites/{site_id}/lists/{list_id}/export',
      methods: [HttpMethod.POST],
      integration,
      authorizer: jwtAuthorizer,
    });
  }
}
