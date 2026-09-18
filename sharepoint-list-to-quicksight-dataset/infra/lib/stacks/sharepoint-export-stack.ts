import { CfnOutput, Fn, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Secret } from 'aws-cdk-lib/aws-secretsmanager';
import {
  SharePointExportConfig,
  createConstructId,
  getRemovalPolicy,
} from '../common/config';
import { StorageBuckets } from '../construct-groups/storage-buckets';
import { ApiGatewayLambda } from '../constructs/api-gateway-lambda';

export interface SharePointExportStackProps extends StackProps {
  readonly config: SharePointExportConfig;
}

export class SharePointExportStack extends Stack {
  constructor(
    scope: Construct,
    id: string,
    { config, ...props }: SharePointExportStackProps,
  ) {
    super(scope, id, props);

    const {
      projectName,
      retainResources,
      entraTenantId,
      entraClientId,
    } = config;
    const account = this.account;
    const region = this.region;
    const removalPolicy = getRemovalPolicy(retainResources);
    const tokenUrl = `https://login.microsoftonline.com/${entraTenantId}/oauth2/v2.0/token`;
    const authorizationUrl = `https://login.microsoftonline.com/${entraTenantId}/oauth2/v2.0/authorize`;

    const storage = new StorageBuckets(
      this,
      createConstructId('Storage'),
      {
        projectName,
        removalPolicy,
        retainResources,
        account,
        region,
      },
    );

    const entraClientSecret = new Secret(
      this,
      createConstructId('EntraClientSecret'),
      {
        secretName: `${projectName}/entra-client-secret`,
        description: 'Entra app client secret for OBO token exchange',
        removalPolicy,
      },
    );

    const apiGateway = new ApiGatewayLambda(
      this,
      createConstructId('Api'),
      {
        projectName,
        exportBucket: storage.exportBucket,
        entraClientSecret,
        entraTokenUrl: tokenUrl,
        entraClientId,
        entraTenantId,
        account,
      },
    );

    new CfnOutput(this, 'ApiEndpoint', {
      value: apiGateway.api.apiEndpoint,
      description: 'API endpoint for Amazon Quick OpenAPI integration',
    });

    new CfnOutput(this, 'QuickIntegrationClientId', {
      value: entraClientId,
      description: 'Amazon Quick integration — Client ID',
    });

    new CfnOutput(this, 'QuickIntegrationTokenUrl', {
      value: tokenUrl,
      description: 'Amazon Quick integration — Token URL',
    });

    new CfnOutput(this, 'QuickIntegrationAuthorizationUrl', {
      value: authorizationUrl,
      description: 'Amazon Quick integration — Authorization URL',
    });

    new CfnOutput(this, 'ExportBucketName', {
      value: storage.exportBucket.bucketName,
      description: 'S3 bucket for exported CSV and manifest files',
    });

    new CfnOutput(this, 'ResolvedOpenApiSpec', {
      value: Fn.sub(
        JSON.stringify({
          openapi: '3.0.3',
          info: { title: 'SharePoint List Export API', version: '1.0.0' },
          servers: [{ url: '${ApiUrl}' }],
          paths: {
            '/sites': {
              get: {
                operationId: 'search_sites',
                description: 'Search for SharePoint sites the authenticated user has access to.',
                parameters: [{ name: 'query', in: 'query', required: true, schema: { type: 'string' } }],
                responses: { '200': { description: 'Matching sites', content: { 'application/json': { schema: { type: 'object', properties: { sites: { type: 'object' } } } } } } },
                security: [{ entra_oauth: [`api://${entraClientId}/access_as_user`] }],
              },
            },
            '/sites/{site_id}/lists': {
              get: {
                operationId: 'list_lists',
                description: 'Get all lists in a SharePoint site.',
                parameters: [{ name: 'site_id', in: 'path', required: true, schema: { type: 'string' } }],
                responses: { '200': { description: 'Lists in the site', content: { 'application/json': { schema: { type: 'object', properties: { lists: { type: 'object' } } } } } } },
                security: [{ entra_oauth: [`api://${entraClientId}/access_as_user`] }],
              },
            },
            '/sites/{site_id}/lists/{list_id}/export': {
              post: {
                operationId: 'export_list',
                description: 'Export a SharePoint list to CSV in S3 with a Quick Sight manifest.',
                parameters: [
                  { name: 'site_id', in: 'path', required: true, schema: { type: 'string' } },
                  { name: 'list_id', in: 'path', required: true, schema: { type: 'string' } },
                ],
                requestBody: { required: true, content: { 'application/json': { schema: { type: 'object', properties: { confirm: { type: 'boolean', description: 'Set to true to confirm the export.' } }, required: ['confirm'] } } } },
                responses: { '200': { description: 'Export result', content: { 'application/json': { schema: { type: 'object', properties: { s3_bucket: { type: 'string' }, csv_s3_key: { type: 'string' }, manifest_s3_key: { type: 'string' }, manifest_s3_uri: { type: 'string' }, row_count: { type: 'integer' }, column_count: { type: 'integer' }, columns: { type: 'array', items: { type: 'string' } }, exported_at: { type: 'string', format: 'date-time' }, instructions: { type: 'string' } } } } } } },
                security: [{ entra_oauth: [`api://${entraClientId}/access_as_user`] }],
              },
            },
          },
          components: {
            securitySchemes: {
              entra_oauth: {
                type: 'oauth2',
                flows: {
                  authorizationCode: {
                    authorizationUrl: `https://login.microsoftonline.com/${entraTenantId}/oauth2/v2.0/authorize`,
                    tokenUrl: `https://login.microsoftonline.com/${entraTenantId}/oauth2/v2.0/token`,
                    scopes: { [`api://${entraClientId}/access_as_user`]: 'Access SharePoint data on behalf of the user' },
                  },
                },
              },
            },
          },
        }),
        { ApiUrl: apiGateway.api.apiEndpoint },
      ),
      description: 'Resolved OpenAPI spec for Amazon Quick integration',
    });
  }
}
