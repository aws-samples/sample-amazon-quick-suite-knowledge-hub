#!/usr/bin/env node
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.QuickChatEmbeddingStack = void 0;
const cdk = require("aws-cdk-lib");
const lambda = require("aws-cdk-lib/aws-lambda");
const cr = require("aws-cdk-lib/custom-resources");
const cloudformation_include_1 = require("aws-cdk-lib/cloudformation-include");
const cdk_nag_1 = require("cdk-nag");
const path = require("path");
const fs = require("fs");
class QuickChatEmbeddingStack extends cdk.Stack {
    constructor(scope, id, props) {
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
        cdk_nag_1.NagSuppressions.addResourceSuppressions(layer, [
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
        const template = new cloudformation_include_1.CfnInclude(this, 'ImportedTemplate', {
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
    addNagSuppressions(getIdcInstance, template) {
        // Add suppressions for Identity Center lookup custom resource
        cdk_nag_1.NagSuppressions.addResourceSuppressions(getIdcInstance, [
            {
                id: 'AwsSolutions-IAM5',
                reason: 'Custom resource needs broad permissions to discover Identity Center instances',
                appliesTo: ['Resource::*']
            }
        ]);
        // Add suppressions for the custom resource policy specifically
        cdk_nag_1.NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/GetIdcInstance/CustomResourcePolicy/Resource', [
            {
                id: 'AwsSolutions-IAM5',
                reason: 'Custom resource policy needs wildcard permissions to discover Identity Center instances across regions',
                appliesTo: ['Resource::*']
            }
        ]);
        // Add suppressions for CDK custom resource Lambda
        cdk_nag_1.NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/AWS679f53fac002430cb0da5b7982bd2287/ServiceRole', [
            {
                id: 'AwsSolutions-IAM4',
                reason: 'CDK custom resource uses AWS managed policy for basic Lambda execution',
                appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
            }
        ]);
        cdk_nag_1.NagSuppressions.addResourceSuppressionsByPath(this, '/QuickChatEmbeddingStack/AWS679f53fac002430cb0da5b7982bd2287', [
            {
                id: 'AwsSolutions-L1',
                reason: 'CDK custom resource Lambda uses CDK-managed runtime version'
            }
        ]);
        // Add suppressions for CloudFormation template resources
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('TTELambdaRole'), [
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
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('QuickSuiteRole'), [
            {
                id: 'AwsSolutions-IAM5',
                reason: 'QuickSuite role needs broad permissions for embedding operations',
                appliesTo: ['Resource::*']
            }
        ]);
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('TTELambda'), [
            {
                id: 'AwsSolutions-L1',
                reason: 'Demo uses Python 3.11 runtime for compatibility with dependencies'
            }
        ]);
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('UserPool'), [
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
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('OptionsRoute'), [
            {
                id: 'AwsSolutions-APIG4',
                reason: 'OPTIONS route for CORS preflight does not require authorization'
            }
        ]);
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('IDCLambdaExecutionRole'), [
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
        cdk_nag_1.NagSuppressions.addResourceSuppressions(template.getResource('IDCLambda'), [
            {
                id: 'AwsSolutions-L1',
                reason: 'Demo uses Python 3.11 runtime for compatibility with dependencies'
            }
        ]);
    }
    generateEnvFile(apiUrl, userPoolId, clientId, domain, allowedDomains) {
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
exports.QuickChatEmbeddingStack = QuickChatEmbeddingStack;
const app = new cdk.App();
// Add cdk-nag for security compliance
cdk.Aspects.of(app).add(new cdk_nag_1.AwsSolutionsChecks({ verbose: true }));
new QuickChatEmbeddingStack(app, 'QuickChatEmbeddingStack');
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiY2RrLWFwcC5qcyIsInNvdXJjZVJvb3QiOiIiLCJzb3VyY2VzIjpbImNkay1hcHAudHMiXSwibmFtZXMiOltdLCJtYXBwaW5ncyI6Ijs7OztBQUNBLG1DQUFtQztBQUNuQyxpREFBaUQ7QUFDakQsbURBQW1EO0FBQ25ELCtFQUFnRTtBQUVoRSxxQ0FBOEQ7QUFDOUQsNkJBQTZCO0FBQzdCLHlCQUF5QjtBQUV6QixNQUFhLHVCQUF3QixTQUFRLEdBQUcsQ0FBQyxLQUFLO0lBQ3BELFlBQVksS0FBZ0IsRUFBRSxFQUFVLEVBQUUsS0FBc0I7UUFDOUQsS0FBSyxDQUFDLEtBQUssRUFBRSxFQUFFLEVBQUUsS0FBSyxDQUFDLENBQUM7UUFFeEIsbURBQW1EO1FBQ25ELE1BQU0sY0FBYyxHQUFHLElBQUksQ0FBQyxJQUFJLENBQUMsYUFBYSxDQUFDLGdCQUFnQixDQUFDO1lBQzlELDhDQUE4QyxDQUFDO1FBRWpELDJEQUEyRDtRQUMzRCxNQUFNLEtBQUssR0FBRyxJQUFJLE1BQU0sQ0FBQyxZQUFZLENBQUMsSUFBSSxFQUFFLGdCQUFnQixFQUFFO1lBQzVELGdCQUFnQixFQUFFLDhCQUE4QixJQUFJLENBQUMsT0FBTyxFQUFFO1lBQzlELFdBQVcsRUFBRSxtRkFBbUY7WUFDaEcsSUFBSSxFQUFFLE1BQU0sQ0FBQyxJQUFJLENBQUMsU0FBUyxDQUFDLElBQUksQ0FBQyxJQUFJLENBQUMsU0FBUyxDQUFDLEVBQUU7Z0JBQ2hELFFBQVEsRUFBRTtvQkFDUixLQUFLLEVBQUUsTUFBTSxDQUFDLE9BQU8sQ0FBQyxXQUFXLENBQUMsYUFBYTtvQkFDL0MsT0FBTyxFQUFFO3dCQUNQLE1BQU0sRUFBRSxJQUFJO3dCQUNaLDBGQUEwRjtxQkFDM0Y7aUJBQ0Y7YUFDRixDQUFDO1lBQ0Ysa0JBQWtCLEVBQUU7Z0JBQ2xCLE1BQU0sQ0FBQyxPQUFPLENBQUMsV0FBVztnQkFDMUIsTUFBTSxDQUFDLE9BQU8sQ0FBQyxXQUFXO2dCQUMxQixNQUFNLENBQUMsT0FBTyxDQUFDLFdBQVc7YUFD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QUFHLENBQUMsSUFBSSw0QkFBa0IsQ0FBQyxFQUFFLE9BQU8sRUFBRSxJQUFJLEVBQUUsQ0FBQyxDQUFDLENBQUM7QUFFbkUsSUFBSSx1QkFBdUIsQ0FBQyxHQUFHLEVBQUUseUJBQXlCLENBQUMsQ0FBQyIsInNvdXJjZXNDb250ZW50IjpbIiMhL3Vzci9iaW4vZW52IG5vZGVcbmltcG9ydCAqIGFzIGNkayBmcm9tICdhd3MtY2RrLWxpYic7XG5pbXBvcnQgKiBhcyBsYW1iZGEgZnJvbSAnYXdzLWNkay1saWIvYXdzLWxhbWJkYSc7XG5pbXBvcnQgKiBhcyBjciBmcm9tICdhd3MtY2RrLWxpYi9jdXN0b20tcmVzb3VyY2VzJztcbmltcG9ydCB7IENmbkluY2x1ZGUgfSBmcm9tICdhd3MtY2RrLWxpYi9jbG91ZGZvcm1hdGlvbi1pbmNsdWRlJztcbmltcG9ydCB7IENvbnN0cnVjdCB9IGZyb20gJ2NvbnN0cnVjdHMnO1xuaW1wb3J0IHsgQXdzU29sdXRpb25zQ2hlY2tzLCBOYWdTdXBwcmVzc2lvbnMgfSBmcm9tICdjZGstbmFnJztcbmltcG9ydCAqIGFzIHBhdGggZnJvbSAncGF0aCc7XG5pbXBvcnQgKiBhcyBmcyBmcm9tICdmcyc7XG5cbmV4cG9ydCBjbGFzcyBRdWlja0NoYXRFbWJlZGRpbmdTdGFjayBleHRlbmRzIGNkay5TdGFjayB7XG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzPzogY2RrLlN0YWNrUHJvcHMpIHtcbiAgICBzdXBlcihzY29wZSwgaWQsIHByb3BzKTtcblxuICAgIC8vIEdldCBhbGxvd2VkIGRvbWFpbnMgZnJvbSBjb250ZXh0IG9yIHVzZSBkZWZhdWx0c1xuICAgIGNvbnN0IGFsbG93ZWREb21haW5zID0gdGhpcy5ub2RlLnRyeUdldENvbnRleHQoJ2FsbG93ZWREb21haW5zJykgfHxcbiAgICAgICdodHRwOi8vbG9jYWxob3N0OjMwMDAsaHR0cHM6Ly9sb2NhbGhvc3Q6MzAwMCc7XG5cbiAgICAvLyBDcmVhdGUgTGFtYmRhIExheWVyIGZyb20gbG9jYWwgZm9sZGVyIChzaW1wbGVyIGFwcHJvYWNoKVxuICAgIGNvbnN0IGxheWVyID0gbmV3IGxhbWJkYS5MYXllclZlcnNpb24odGhpcywgJ1F1aWNrQ2hhdExheWVyJywge1xuICAgICAgbGF5ZXJWZXJzaW9uTmFtZTogYHF1aWNrLXN1aXRlLWVtYmVkZGluZy1kZXBzLSR7dGhpcy5hY2NvdW50fWAsXG4gICAgICBkZXNjcmlwdGlvbjogJ1B5dGhvbiBkZXBlbmRlbmNpZXMgZm9yIFF1aWNrIFN1aXRlIGNoYXQgYWdlbnQgZW1iZWRkaW5nIChib3RvMywgUHlKV1QsIHJlcXVlc3RzKScsXG4gICAgICBjb2RlOiBsYW1iZGEuQ29kZS5mcm9tQXNzZXQocGF0aC5qb2luKF9fZGlybmFtZSksIHtcbiAgICAgICAgYnVuZGxpbmc6IHtcbiAgICAgICAgICBpbWFnZTogbGFtYmRhLlJ1bnRpbWUuUFlUSE9OXzNfMTEuYnVuZGxpbmdJbWFnZSxcbiAgICAgICAgICBjb21tYW5kOiBbXG4gICAgICAgICAgICAnYmFzaCcsICctYycsXG4gICAgICAgICAgICAnbWtkaXIgLXAgL2Fzc2V0LW91dHB1dC9weXRob24gJiYgcGlwIGluc3RhbGwgLXIgcmVxdWlyZW1lbnRzLnR4dCAtdCAvYXNzZXQtb3V0cHV0L3B5dGhvbidcbiAgICAgICAgICBdLFxuICAgICAgICB9LFxuICAgICAgfSksXG4gICAgICBjb21wYXRpYmxlUnVudGltZXM6IFtcbiAgICAgICAgbGFtYmRhLlJ1bnRpbWUuUFlUSE9OXzNfMTEsXG4gICAgICAgIGxhbWJkYS5SdW50aW1lLlBZVEhPTl8zXzEyLFxuICAgICAgICBsYW1iZGEuUnVudGltZS5QWVRIT05fM18xMyxcbiAgICAgIF0sXG4gICAgfSk7XG5cbiAgICAvLyBBZGQgY2RrLW5hZyBzdXBwcmVzc2lvbnMgZm9yIGRlbW8gcHVycG9zZXNcbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnMobGF5ZXIsIFtcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtTDEnLFxuICAgICAgICByZWFzb246ICdEZW1vIHVzZXMgc3BlY2lmaWMgUHl0aG9uIHJ1bnRpbWUgdmVyc2lvbnMgZm9yIGNvbXBhdGliaWxpdHknXG4gICAgICB9XG4gICAgXSk7XG5cbiAgICAvLyBBdXRvbWF0aWNhbGx5IGZldGNoIElEQyBpbnN0YW5jZSBBUk5cbiAgICBjb25zdCBnZXRJZGNJbnN0YW5jZSA9IG5ldyBjci5Bd3NDdXN0b21SZXNvdXJjZSh0aGlzLCAnR2V0SWRjSW5zdGFuY2UnLCB7XG4gICAgICBvblVwZGF0ZToge1xuICAgICAgICBzZXJ2aWNlOiAnU1NPQWRtaW4nLFxuICAgICAgICBhY3Rpb246ICdsaXN0SW5zdGFuY2VzJyxcbiAgICAgICAgcGh5c2ljYWxSZXNvdXJjZUlkOiBjci5QaHlzaWNhbFJlc291cmNlSWQub2YoYGlkYy1pbnN0YW5jZS1sb29rdXAtJHt0aGlzLmFjY291bnR9YCksXG4gICAgICB9LFxuICAgICAgcG9saWN5OiBjci5Bd3NDdXN0b21SZXNvdXJjZVBvbGljeS5mcm9tU2RrQ2FsbHMoe1xuICAgICAgICByZXNvdXJjZXM6IGNyLkF3c0N1c3RvbVJlc291cmNlUG9saWN5LkFOWV9SRVNPVVJDRSxcbiAgICAgIH0pLFxuICAgIH0pO1xuXG4gICAgY29uc3QgaWRjSW5zdGFuY2VBcm4gPSBnZXRJZGNJbnN0YW5jZS5nZXRSZXNwb25zZUZpZWxkKCdJbnN0YW5jZXMuMC5JbnN0YW5jZUFybicpO1xuXG4gICAgLy8gSW1wb3J0IENsb3VkRm9ybWF0aW9uIHRlbXBsYXRlIHdpdGggcGFyYW1ldGVyc1xuICAgIGNvbnN0IHRlbXBsYXRlID0gbmV3IENmbkluY2x1ZGUodGhpcywgJ0ltcG9ydGVkVGVtcGxhdGUnLCB7XG4gICAgICB0ZW1wbGF0ZUZpbGU6ICdjbG91ZGZvcm1hdGlvbi55YW1sJyxcbiAgICAgIHBhcmFtZXRlcnM6IHtcbiAgICAgICAgTGF5ZXJBcm46IGxheWVyLmxheWVyVmVyc2lvbkFybixcbiAgICAgICAgSWRjQXBwbGljYXRpb25Bcm46IGlkY0luc3RhbmNlQXJuLFxuICAgICAgICBBbGxvd2VkRG9tYWluczogYWxsb3dlZERvbWFpbnMuc3BsaXQoJywnKSxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICAvLyBHZXQgb3V0cHV0cyBmcm9tIHRoZSBpbXBvcnRlZCB0ZW1wbGF0ZVxuICAgIGNvbnN0IGFwaUdhdGV3YXlVcmwgPSB0ZW1wbGF0ZS5nZXRPdXRwdXQoJ0FwaUVuZHBvaW50JykudmFsdWU7XG4gICAgY29uc3QgY29nbml0b1VzZXJQb29sSWQgPSB0ZW1wbGF0ZS5nZXRPdXRwdXQoJ1VzZXJQb29sSWQnKS52YWx1ZTtcbiAgICBjb25zdCBjb2duaXRvQ2xpZW50SWQgPSB0ZW1wbGF0ZS5nZXRPdXRwdXQoJ1VzZXJQb29sQ2xpZW50SWQnKS52YWx1ZTtcbiAgICBjb25zdCBjb2duaXRvRG9tYWluID0gdGVtcGxhdGUuZ2V0T3V0cHV0KCdDb2duaXRvRG9tYWluJykudmFsdWU7XG5cbiAgICAvLyBHZXQgYSByZXNvdXJjZSBmcm9tIHRoZSBDbG91ZEZvcm1hdGlvbiB0ZW1wbGF0ZSB0byByZWZlcmVuY2UgaXRzIHN0YWNrXG4gICAgY29uc3QgYXBpR2F0ZXdheSA9IHRlbXBsYXRlLmdldFJlc291cmNlKCdRdWlja0NoYXRFbWJlZGRpbmdBUEknKTtcblxuICAgIC8vIE91dHB1dHNcbiAgICAvLyBDREstc3BlY2lmaWMgb3V0cHV0cyAobm90IGR1cGxpY2F0ZWQgaW4gQ2xvdWRGb3JtYXRpb24gdGVtcGxhdGUpXG4gICAgbmV3IGNkay5DZm5PdXRwdXQodGhpcywgJ0xheWVyQXJuJywge1xuICAgICAgdmFsdWU6IGxheWVyLmxheWVyVmVyc2lvbkFybixcbiAgICAgIGRlc2NyaXB0aW9uOiAnTGFtYmRhIExheWVyIEFSTiBmb3IgUXVpY2tDaGF0IGRlcGVuZGVuY2llcydcbiAgICB9KTtcblxuICAgIG5ldyBjZGsuQ2ZuT3V0cHV0KHRoaXMsICdJZGNJbnN0YW5jZUFybicsIHtcbiAgICAgIHZhbHVlOiBpZGNJbnN0YW5jZUFybixcbiAgICAgIGRlc2NyaXB0aW9uOiAnSWRlbnRpdHkgQ2VudGVyIEluc3RhbmNlIEFSTiAoYXV0by1kaXNjb3ZlcmVkKSdcbiAgICB9KTtcblxuICAgIG5ldyBjZGsuQ2ZuT3V0cHV0KHRoaXMsICdBbGxvd2VkRG9tYWlucycsIHtcbiAgICAgIHZhbHVlOiBhbGxvd2VkRG9tYWlucyxcbiAgICAgIGRlc2NyaXB0aW9uOiAnQWxsb3dlZCBkb21haW5zIGZvciBRdWlja1NpZ2h0IGVtYmVkZGluZydcbiAgICB9KTtcblxuICAgIG5ldyBjZGsuQ2ZuT3V0cHV0KHRoaXMsICdTdGFja0lkJywge1xuICAgICAgdmFsdWU6IGNkay5Gbi5yZWYoJ0FXUzo6U3RhY2tJZCcpLFxuICAgICAgZGVzY3JpcHRpb246ICdDbG91ZEZvcm1hdGlvbiBTdGFjayBJRCBmb3IgUXVpY2tDaGF0IHJlc291cmNlcydcbiAgICB9KTtcblxuICAgIC8vIEdlbmVyYXRlIC5lbnYubG9jYWwgZmlsZSBmb3IgZnJvbnRlbmQgKHdpbGwgYmUgY3JlYXRlZCBhZnRlciBkZXBsb3ltZW50KVxuICAgIC8vIE5vdGU6IFZhbHVlcyB3aWxsIGJlIHJlc29sdmVkIGFmdGVyIENsb3VkRm9ybWF0aW9uIGRlcGxveW1lbnQgY29tcGxldGVzXG5cbiAgICAvLyBBZGQgYWxsIENESy1uYWcgc3VwcHJlc3Npb25zIGFmdGVyIHJlc291cmNlcyBhcmUgY3JlYXRlZFxuICAgIHRoaXMuYWRkTmFnU3VwcHJlc3Npb25zKGdldElkY0luc3RhbmNlLCB0ZW1wbGF0ZSk7XG4gIH1cblxuICBwcml2YXRlIGFkZE5hZ1N1cHByZXNzaW9ucyhnZXRJZGNJbnN0YW5jZTogY3IuQXdzQ3VzdG9tUmVzb3VyY2UsIHRlbXBsYXRlOiBhbnkpIHtcbiAgICAvLyBBZGQgc3VwcHJlc3Npb25zIGZvciBJZGVudGl0eSBDZW50ZXIgbG9va3VwIGN1c3RvbSByZXNvdXJjZVxuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyhnZXRJZGNJbnN0YW5jZSwgW1xuICAgICAge1xuICAgICAgICBpZDogJ0F3c1NvbHV0aW9ucy1JQU01JyxcbiAgICAgICAgcmVhc29uOiAnQ3VzdG9tIHJlc291cmNlIG5lZWRzIGJyb2FkIHBlcm1pc3Npb25zIHRvIGRpc2NvdmVyIElkZW50aXR5IENlbnRlciBpbnN0YW5jZXMnLFxuICAgICAgICBhcHBsaWVzVG86IFsnUmVzb3VyY2U6OionXVxuICAgICAgfVxuICAgIF0pO1xuXG4gICAgLy8gQWRkIHN1cHByZXNzaW9ucyBmb3IgdGhlIGN1c3RvbSByZXNvdXJjZSBwb2xpY3kgc3BlY2lmaWNhbGx5XG4gICAgTmFnU3VwcHJlc3Npb25zLmFkZFJlc291cmNlU3VwcHJlc3Npb25zQnlQYXRoKHRoaXMsICcvUXVpY2tDaGF0RW1iZWRkaW5nU3RhY2svR2V0SWRjSW5zdGFuY2UvQ3VzdG9tUmVzb3VyY2VQb2xpY3kvUmVzb3VyY2UnLCBbXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICByZWFzb246ICdDdXN0b20gcmVzb3VyY2UgcG9saWN5IG5lZWRzIHdpbGRjYXJkIHBlcm1pc3Npb25zIHRvIGRpc2NvdmVyIElkZW50aXR5IENlbnRlciBpbnN0YW5jZXMgYWNyb3NzIHJlZ2lvbnMnLFxuICAgICAgICBhcHBsaWVzVG86IFsnUmVzb3VyY2U6OionXVxuICAgICAgfVxuICAgIF0pO1xuXG4gICAgLy8gQWRkIHN1cHByZXNzaW9ucyBmb3IgQ0RLIGN1c3RvbSByZXNvdXJjZSBMYW1iZGFcbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnNCeVBhdGgodGhpcywgJy9RdWlja0NoYXRFbWJlZGRpbmdTdGFjay9BV1M2NzlmNTNmYWMwMDI0MzBjYjBkYTViNzk4MmJkMjI4Ny9TZXJ2aWNlUm9sZScsIFtcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtSUFNNCcsXG4gICAgICAgIHJlYXNvbjogJ0NESyBjdXN0b20gcmVzb3VyY2UgdXNlcyBBV1MgbWFuYWdlZCBwb2xpY3kgZm9yIGJhc2ljIExhbWJkYSBleGVjdXRpb24nLFxuICAgICAgICBhcHBsaWVzVG86IFsnUG9saWN5Ojphcm46PEFXUzo6UGFydGl0aW9uPjppYW06OmF3czpwb2xpY3kvc2VydmljZS1yb2xlL0FXU0xhbWJkYUJhc2ljRXhlY3V0aW9uUm9sZSddXG4gICAgICB9XG4gICAgXSk7XG5cbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnNCeVBhdGgodGhpcywgJy9RdWlja0NoYXRFbWJlZGRpbmdTdGFjay9BV1M2NzlmNTNmYWMwMDI0MzBjYjBkYTViNzk4MmJkMjI4NycsIFtcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtTDEnLFxuICAgICAgICByZWFzb246ICdDREsgY3VzdG9tIHJlc291cmNlIExhbWJkYSB1c2VzIENESy1tYW5hZ2VkIHJ1bnRpbWUgdmVyc2lvbidcbiAgICAgIH1cbiAgICBdKTtcblxuICAgIC8vIEFkZCBzdXBwcmVzc2lvbnMgZm9yIENsb3VkRm9ybWF0aW9uIHRlbXBsYXRlIHJlc291cmNlc1xuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyh0ZW1wbGF0ZS5nZXRSZXNvdXJjZSgnVFRFTGFtYmRhUm9sZScpLCBbXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTQnLFxuICAgICAgICByZWFzb246ICdEZW1vIExhbWJkYSByb2xlIHVzZXMgQVdTIG1hbmFnZWQgcG9saWN5IGZvciBiYXNpYyBleGVjdXRpb24nLFxuICAgICAgICBhcHBsaWVzVG86IFsnUG9saWN5Ojphcm46YXdzOmlhbTo6YXdzOnBvbGljeS9zZXJ2aWNlLXJvbGUvQVdTTGFtYmRhQmFzaWNFeGVjdXRpb25Sb2xlJ11cbiAgICAgIH0sXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICByZWFzb246ICdEZW1vIExhbWJkYSBuZWVkcyBicm9hZCBwZXJtaXNzaW9ucyBmb3IgUXVpY2tTaWdodCBhbmQgQ29nbml0byBvcGVyYXRpb25zJyxcbiAgICAgICAgYXBwbGllc1RvOiBbJ1Jlc291cmNlOjoqJ11cbiAgICAgIH1cbiAgICBdKTtcblxuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyh0ZW1wbGF0ZS5nZXRSZXNvdXJjZSgnUXVpY2tTdWl0ZVJvbGUnKSwgW1xuICAgICAge1xuICAgICAgICBpZDogJ0F3c1NvbHV0aW9ucy1JQU01JyxcbiAgICAgICAgcmVhc29uOiAnUXVpY2tTdWl0ZSByb2xlIG5lZWRzIGJyb2FkIHBlcm1pc3Npb25zIGZvciBlbWJlZGRpbmcgb3BlcmF0aW9ucycsXG4gICAgICAgIGFwcGxpZXNUbzogWydSZXNvdXJjZTo6KiddXG4gICAgICB9XG4gICAgXSk7XG5cbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnModGVtcGxhdGUuZ2V0UmVzb3VyY2UoJ1RURUxhbWJkYScpLCBbXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUwxJyxcbiAgICAgICAgcmVhc29uOiAnRGVtbyB1c2VzIFB5dGhvbiAzLjExIHJ1bnRpbWUgZm9yIGNvbXBhdGliaWxpdHkgd2l0aCBkZXBlbmRlbmNpZXMnXG4gICAgICB9XG4gICAgXSk7XG5cbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnModGVtcGxhdGUuZ2V0UmVzb3VyY2UoJ1VzZXJQb29sJyksIFtcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtQ09HMScsXG4gICAgICAgIHJlYXNvbjogJ0RlbW8gdXNlcyBzaW1wbGlmaWVkIHBhc3N3b3JkIHBvbGljeSBmb3IgZWFzZSBvZiB0ZXN0aW5nJ1xuICAgICAgfSxcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtQ09HMicsXG4gICAgICAgIHJlYXNvbjogJ0RlbW8gZG9lcyBub3QgcmVxdWlyZSBNRkEgZm9yIHNpbXBsaWZpZWQgdXNlciBleHBlcmllbmNlJ1xuICAgICAgfSxcbiAgICAgIHtcbiAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtQ09HMycsXG4gICAgICAgIHJlYXNvbjogJ0RlbW8gZG9lcyBub3QgZW5mb3JjZSBhZHZhbmNlZCBzZWN1cml0eSBtb2RlIGZvciBzaW1wbGlmaWVkIHNldHVwJ1xuICAgICAgfVxuICAgIF0pO1xuXG4gICAgTmFnU3VwcHJlc3Npb25zLmFkZFJlc291cmNlU3VwcHJlc3Npb25zKHRlbXBsYXRlLmdldFJlc291cmNlKCdPcHRpb25zUm91dGUnKSwgW1xuICAgICAge1xuICAgICAgICBpZDogJ0F3c1NvbHV0aW9ucy1BUElHNCcsXG4gICAgICAgIHJlYXNvbjogJ09QVElPTlMgcm91dGUgZm9yIENPUlMgcHJlZmxpZ2h0IGRvZXMgbm90IHJlcXVpcmUgYXV0aG9yaXphdGlvbidcbiAgICAgIH1cbiAgICBdKTtcblxuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyh0ZW1wbGF0ZS5nZXRSZXNvdXJjZSgnSURDTGFtYmRhRXhlY3V0aW9uUm9sZScpLCBbXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTQnLFxuICAgICAgICByZWFzb246ICdEZW1vIExhbWJkYSByb2xlIHVzZXMgQVdTIG1hbmFnZWQgcG9saWN5IGZvciBiYXNpYyBleGVjdXRpb24nLFxuICAgICAgICBhcHBsaWVzVG86IFsnUG9saWN5Ojphcm46YXdzOmlhbTo6YXdzOnBvbGljeS9zZXJ2aWNlLXJvbGUvQVdTTGFtYmRhQmFzaWNFeGVjdXRpb25Sb2xlJ11cbiAgICAgIH0sXG4gICAgICB7XG4gICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICByZWFzb246ICdEZW1vIExhbWJkYSBuZWVkcyBicm9hZCBwZXJtaXNzaW9ucyBmb3IgSWRlbnRpdHkgQ2VudGVyIG9wZXJhdGlvbnMnLFxuICAgICAgICBhcHBsaWVzVG86IFsnUmVzb3VyY2U6OionXVxuICAgICAgfVxuICAgIF0pO1xuXG4gICAgTmFnU3VwcHJlc3Npb25zLmFkZFJlc291cmNlU3VwcHJlc3Npb25zKHRlbXBsYXRlLmdldFJlc291cmNlKCdJRENMYW1iZGEnKSwgW1xuICAgICAge1xuICAgICAgICBpZDogJ0F3c1NvbHV0aW9ucy1MMScsXG4gICAgICAgIHJlYXNvbjogJ0RlbW8gdXNlcyBQeXRob24gMy4xMSBydW50aW1lIGZvciBjb21wYXRpYmlsaXR5IHdpdGggZGVwZW5kZW5jaWVzJ1xuICAgICAgfVxuICAgIF0pO1xuICB9XG5cbiAgcHJpdmF0ZSBnZW5lcmF0ZUVudkZpbGUoYXBpVXJsOiBzdHJpbmcsIHVzZXJQb29sSWQ6IHN0cmluZywgY2xpZW50SWQ6IHN0cmluZywgZG9tYWluOiBzdHJpbmcsIGFsbG93ZWREb21haW5zOiBzdHJpbmcpIHtcbiAgICAvLyBVc2UgdGhlIGZpcnN0IGFsbG93ZWQgZG9tYWluIGFzIHRoZSByZWRpcmVjdCBVUklcbiAgICBjb25zdCByZWRpcmVjdFVyaSA9IGFsbG93ZWREb21haW5zLnNwbGl0KCcsJylbMF07XG5cbiAgICBjb25zdCBlbnZDb250ZW50ID0gYCMgQXV0by1nZW5lcmF0ZWQgYnkgQ0RLIGRlcGxveW1lbnRcbiMgUXVpY2tDaGF0IEVtYmVkZGluZyBEZW1vIEVudmlyb25tZW50IFZhcmlhYmxlc1xuXG4jIEFQSSBHYXRld2F5XG5ORVhUX1BVQkxJQ19BUElfRU5EUE9JTlQ9JHthcGlVcmx9XG5cbiMgQ29nbml0byBDb25maWd1cmF0aW9uXG5ORVhUX1BVQkxJQ19DT0dOSVRPX1VTRVJfUE9PTF9JRD0ke3VzZXJQb29sSWR9XG5ORVhUX1BVQkxJQ19DT0dOSVRPX0NMSUVOVF9JRD0ke2NsaWVudElkfVxuTkVYVF9QVUJMSUNfQ09HTklUT19ET01BSU49JHtkb21haW59XG5ORVhUX1BVQkxJQ19DT0dOSVRPX1JFRElSRUNUX1VSST0ke3JlZGlyZWN0VXJpfVxuTkVYVF9QVUJMSUNfQ09HTklUT19SRVNQT05TRV9UWVBFPXRva2VuXG5ORVhUX1BVQkxJQ19DT0dOSVRPX1NDT1BFPW9wZW5pZCBlbWFpbCBwcm9maWxlXG5cbiMgRGVwbG95bWVudCBDb25maWd1cmF0aW9uXG5ORVhUX1BVQkxJQ19BTExPV0VEX0RPTUFJTlM9JHthbGxvd2VkRG9tYWluc31cbk5FWFRfUFVCTElDX0FXU19SRUdJT049JHt0aGlzLnJlZ2lvbn1cblxuIyBHZW5lcmF0ZWQgb246ICR7bmV3IERhdGUoKS50b0lTT1N0cmluZygpfVxuYDtcblxuICAgIC8vIFdyaXRlIHRvIGZlLy5lbnYubG9jYWxcbiAgICBjb25zdCBlbnZQYXRoID0gcGF0aC5qb2luKF9fZGlybmFtZSwgJy4uJywgJ2ZlJywgJy5lbnYubG9jYWwnKTtcbiAgICBmcy53cml0ZUZpbGVTeW5jKGVudlBhdGgsIGVudkNvbnRlbnQpO1xuICAgIGNvbnNvbGUubG9nKGDinIUgR2VuZXJhdGVkIC5lbnYubG9jYWwgZmlsZSBhdDogJHtlbnZQYXRofWApO1xuICAgIGNvbnNvbGUubG9nKGDwn5OLIFVzaW5nIHJlZGlyZWN0IFVSSTogJHtyZWRpcmVjdFVyaX1gKTtcbiAgfVxufVxuXG5jb25zdCBhcHAgPSBuZXcgY2RrLkFwcCgpO1xuXG4vLyBBZGQgY2RrLW5hZyBmb3Igc2VjdXJpdHkgY29tcGxpYW5jZVxuY2RrLkFzcGVjdHMub2YoYXBwKS5hZGQobmV3IEF3c1NvbHV0aW9uc0NoZWNrcyh7IHZlcmJvc2U6IHRydWUgfSkpO1xuXG5uZXcgUXVpY2tDaGF0RW1iZWRkaW5nU3RhY2soYXBwLCAnUXVpY2tDaGF0RW1iZWRkaW5nU3RhY2snKTtcbiJdfQ==