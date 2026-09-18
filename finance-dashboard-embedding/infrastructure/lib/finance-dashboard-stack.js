const cdk = require('aws-cdk-lib');
const lambda = require('aws-cdk-lib/aws-lambda');
const apigateway = require('aws-cdk-lib/aws-apigateway');
const dynamodb = require('aws-cdk-lib/aws-dynamodb');
const s3 = require('aws-cdk-lib/aws-s3');
const cloudfront = require('aws-cdk-lib/aws-cloudfront');
const origins = require('aws-cdk-lib/aws-cloudfront-origins');
const cr = require('aws-cdk-lib/custom-resources');

class FinanceDashboardStack extends cdk.Stack {
  constructor(scope, id, props) {
    super(scope, id, props);

    // DynamoDB Table
    const metricsTable = new dynamodb.Table(this, 'MetricsTable', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY
    });

    // Lambda Function
    const getMetricsFunction = new lambda.Function(this, 'GetMetricsFunction', {
      runtime: lambda.Runtime.NODEJS_18_X,
      handler: 'getMetrics.handler',
      code: lambda.Code.fromAsset('../backend/lambda'),
      environment: {
        METRICS_TABLE_NAME: metricsTable.tableName
      }
    });

    metricsTable.grantReadData(getMetricsFunction);

    // Seed Data Lambda Function
    const seedDataFunction = new lambda.Function(this, 'SeedDataFunction', {
      runtime: lambda.Runtime.NODEJS_18_X,
      handler: 'seedData.handler',
      code: lambda.Code.fromAsset('../backend/lambda'),
      environment: {
        METRICS_TABLE_NAME: metricsTable.tableName
      },
      timeout: cdk.Duration.seconds(30)
    });

    metricsTable.grantWriteData(seedDataFunction);

    // Custom Resource to seed data on deployment
    const seedDataProvider = new cr.Provider(this, 'SeedDataProvider', {
      onEventHandler: seedDataFunction
    });

    new cdk.CustomResource(this, 'SeedDataResource', {
      serviceToken: seedDataProvider.serviceToken
    });

    // API Gateway
    const api = new apigateway.RestApi(this, 'FinanceApi', {
      restApiName: 'Finance Dashboard API',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS
      }
    });

    const metrics = api.root.addResource('metrics');
    metrics.addMethod('GET', new apigateway.LambdaIntegration(getMetricsFunction));

    // S3 Bucket for Frontend (private, accessed via CloudFront OAC)
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true
    });

    // CloudFront Distribution with OAC
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(websiteBucket)
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html'
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html'
        }
      ]
    });

    // Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway URL'
    });

    new cdk.CfnOutput(this, 'DistributionUrl', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'CloudFront Distribution URL'
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: websiteBucket.bucketName,
      description: 'S3 Bucket Name'
    });
  }
}

module.exports = { FinanceDashboardStack };
