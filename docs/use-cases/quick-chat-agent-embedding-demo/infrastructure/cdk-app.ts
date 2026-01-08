#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { CfnInclude } from 'aws-cdk-lib/cloudformation-include';
import { Construct } from 'constructs';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import * as path from 'path';
import * as fs from 'fs';

export class QuickChatEmbeddingStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Get allowed domains from context or use defaults
    const allowedDomains = this.node.tryGetContext('allowedDomains') ||
      'http://localhost:3000,https://localhost:3000';

    // Create Lambda Layer from local folder (simpler approach)
    const layer = new lambda.LayerVersion(this, 'QuickChatLayer', {
      layerVersionName: `quick-suite-embedding-deps-${this.account}`,
      description: 'Python dependencies for Quick Suite chat agent embedding (boto3, PyJWT, requests)',
      code: lambda.Code.fromAsset(path.join(__dirname), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'mkdir -p /asset-output/python && pip install -r requirements.txt -t /asset-output/python'
          ],
        },
      }),
      compatibleRuntimes: [
        lambda.Runtime.PYTHON_3_11,
        lambda.Runtime.PYTHON_3_12,
        lambda.Runtime.PYTHON_3_13,
      ],
    });

    // Add cdk-nag suppressions for demo purposes
    NagSuppressions.addResourceSuppressions(layer, [
      {
        id: 'AwsSolutions-L1',
        reason: 'Demo uses specific Python runtime versions for compatibility'
      }
    ]);

    // Automatically fetch IDC instance ARN
    const getIdcInstance = new cr.AwsCustomResource(this, 'GetIdcInstance', {
      onUpdate: {
        service: 'SSOAdmin',
        action: 'listInstances',
        physicalResourceId: cr.PhysicalResourceId.of(`idc-instance-lookup-${this.account}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
      }),
    });

    const idcInstanceArn = getIdcInstance.getResponseField('Instances.0.InstanceArn');

    // Import CloudFormation template with parameters
    const template = new CfnInclude(this, 'ImportedTemplate', {
      templateFile: 'cloudformation.yaml',
      parameters: {
        LayerArn: layer.layerVersionArn,
        IdcApplicationArn: idcInstanceArn,
        AllowedDomains: allowedDomains.split(','),
      },
    });

    // Get outputs from the imported template
    const apiGatewayUrl = template.getOutput('ApiEndpoint').value;
    const cognitoUserPoolId = template.getOutput('UserPoolId').value;
    const cognitoClientId = template.getOutput('UserPoolClientId').value;
    const cognitoDomain = template.getOutput('CognitoDomain').value;

    // Get a resource from the CloudFormation template to reference its stack
    const apiGateway = template.getResource('QuickChatEmbeddingAPI');

    // Outputs
    // CDK-specific outputs (not duplicated in CloudFormation template)
    new cdk.CfnOutput(this, 'LayerArn', {
      value: layer.layerVersionArn,
      description: 'Lambda Layer ARN for QuickChat dependencies'
    });

    new cdk.CfnOutput(this, 'IdcInstanceArn', {
      value: idcInstanceArn,
      description: 'Identity Center Instance ARN (auto-discovered)'
    });

    new cdk.CfnOutput(this, 'AllowedDomains', {
      value: allowedDomains,
      description: 'Allowed domains for QuickSight embedding'
    });

    new cdk.CfnOutput(this, 'StackId', {
      value: cdk.Fn.ref('AWS::StackId'),
      description: 'CloudFormation Stack ID for QuickChat resources'
    });

    // Generate .env.local file for frontend (will be created after deployment)
    // Note: Values will be resolved after CloudFormation deployment completes

    // Add all CDK-nag suppressions after resources are created
    this.addNagSuppressions(getIdcInstance, template);
  }

  private addNagSuppressions(getIdcInstance: cr.AwsCustomResource, template: any) {
    // Add suppressions for Identity Center lookup custom resource
    NagSuppressions.addResourceSuppressions(getIdcInstance, [
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Custom resource needs broad permissions to discover Identity Center instances',
        appliesTo: ['Resource::*']
      }
    ]);

    // Add suppressions for the custom resource policy specifically
    NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/GetIdcInstance/CustomResourcePolicy/Resource', [
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Custom resource policy needs wildcard permissions to discover Identity Center instances across regions',
        appliesTo: ['Resource::*']
      }
    ]);

    // Add suppressions for CDK custom resource Lambda
    NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/AWS679f53fac002430cb0da5b7982bd2287/ServiceRole', [
      {
        id: 'AwsSolutions-IAM4',
        reason: 'CDK custom resource uses AWS managed policy for basic Lambda execution',
        appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
      }
    ]);

    NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/AWS679f53fac002430cb0da5b7982bd2287', [
      {
        id: 'AwsSolutions-L1',
        reason: 'CDK custom resource Lambda uses CDK-managed runtime version'
      }
    ]);

    // Add suppressions for CloudFormation template resources
    NagSuppressions.addResourceSuppressions(template.getResource('TTELambdaRole'), [
      {
        id: 'AwsSolutions-IAM4',
        reason: 'Demo Lambda role uses AWS managed policy for basic execution',
        appliesTo: ['Policy::arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
      },
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Demo Lambda needs broad permissions for QuickSight and Cognito operations',
        appliesTo: ['Resource::*']
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('QuickSuiteRole'), [
      {
        id: 'AwsSolutions-IAM5',
        reason: 'QuickSuite role needs broad permissions for embedding operations',
        appliesTo: ['Resource::*']
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('TTELambda'), [
      {
        id: 'AwsSolutions-L1',
        reason: 'Demo uses Python 3.11 runtime for compatibility with dependencies'
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('UserPool'), [
      {
        id: 'AwsSolutions-COG1',
        reason: 'Demo uses simplified password policy for ease of testing'
      },
      {
        id: 'AwsSolutions-COG2',
        reason: 'Demo does not require MFA for simplified user experience'
      },
      {
        id: 'AwsSolutions-COG3',
        reason: 'Demo does not enforce advanced security mode for simplified setup'
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('OptionsRoute'), [
      {
        id: 'AwsSolutions-APIG4',
        reason: 'OPTIONS route for CORS preflight does not require authorization'
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('IDCLambdaExecutionRole'), [
      {
        id: 'AwsSolutions-IAM4',
        reason: 'Demo Lambda role uses AWS managed policy for basic execution',
        appliesTo: ['Policy::arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
      },
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Demo Lambda needs broad permissions for Identity Center operations',
        appliesTo: ['Resource::*']
      }
    ]);

    NagSuppressions.addResourceSuppressions(template.getResource('IDCLambda'), [
      {
        id: 'AwsSolutions-L1',
        reason: 'Demo uses Python 3.11 runtime for compatibility with dependencies'
      }
    ]);
  }

  private generateEnvFile(apiUrl: string, userPoolId: string, clientId: string, domain: string, allowedDomains: string) {
    // Use the first allowed domain as the redirect URI
    const redirectUri = allowedDomains.split(',')[0];

    const envContent = `# Auto-generated by CDK deployment
# QuickChat Embedding Demo Environment Variables

# API Gateway
NEXT_PUBLIC_API_ENDPOINT=${apiUrl}

# Cognito Configuration
NEXT_PUBLIC_COGNITO_USER_POOL_ID=${userPoolId}
NEXT_PUBLIC_COGNITO_CLIENT_ID=${clientId}
NEXT_PUBLIC_COGNITO_DOMAIN=${domain}
NEXT_PUBLIC_COGNITO_REDIRECT_URI=${redirectUri}
NEXT_PUBLIC_COGNITO_RESPONSE_TYPE=token
NEXT_PUBLIC_COGNITO_SCOPE=openid email profile

# Deployment Configuration
NEXT_PUBLIC_ALLOWED_DOMAINS=${allowedDomains}
NEXT_PUBLIC_AWS_REGION=${this.region}

# Generated on: ${new Date().toISOString()}
`;

    // Write to fe/.env.local
    const envPath = path.join(__dirname, '..', 'fe', '.env.local');
    fs.writeFileSync(envPath, envContent);
    console.log(`✅ Generated .env.local file at: ${envPath}`);
    console.log(`📋 Using redirect URI: ${redirectUri}`);
  }
}

const app = new cdk.App();

// Add cdk-nag for security compliance
cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

new QuickChatEmbeddingStack(app, 'QuickChatEmbeddingStack');
