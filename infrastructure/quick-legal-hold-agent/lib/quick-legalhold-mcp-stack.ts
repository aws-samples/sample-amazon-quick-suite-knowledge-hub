import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ddb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as firehose from 'aws-cdk-lib/aws-kinesisfirehose';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as path from 'path';

// Unique prefix for THIS standalone stack. Distinct from quick-legalhold-test-*
// so nothing collides with the still-deployed test stack.
const PREFIX = 'quick-legalhold-mcp-';

const SCOPE_NAME = 'invoke';
const RESOURCE_SERVER_ID = 'holds-api';
const FULL_SCOPE = `${RESOURCE_SERVER_ID}/${SCOPE_NAME}`; // holds-api/invoke

// Amazon Quick's MCP OAuth redirect URI (resolved from existing Quick connectors).
const DEFAULT_QUICK_REDIRECT_URI = 'https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback';
const LOCAL_CALLBACK = 'http://localhost:8765/callback';

// TEST-ONLY synthetic 3LO end-user. Created directly in the Cognito pool with a
// permanent password so all connection details can be surfaced as CloudFormation
// outputs. The password is NOT hardcoded — it is supplied at deploy time as a
// NoEcho CloudFormation parameter (or via -c testUserPassword=...), matching the
// migrator stack's TestPassword handling. This is a sample credential for
// evaluation only — never use it for real/production identities.

// IAM Identity Center store id, used only when IDENTITY_MODE=idc (read-only
// resolve/expand). Provide your own at deploy time:
//   cdk deploy -c identityStoreId=d-xxxxxxxxxx
// Leave unset to run in the default 'direct' mode (synthetic ARNs, no IDC calls).

export class QuickLegalholdMcpStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;
    const quickRedirectUri =
      (this.node.tryGetContext('quickRedirectUri') as string) || DEFAULT_QUICK_REDIRECT_URI;

    // TEST-ONLY 3LO user password — supplied at deploy time, never hardcoded.
    // Prefer `-c testUserPassword=...`; otherwise a NoEcho CloudFormation
    // parameter (keeps the value out of the template, console, and events).
    const testUserPasswordParam = new cdk.CfnParameter(this, 'TestUserPassword', {
      type: 'String',
      noEcho: true,
      default: '',
      description:
        'TEST-ONLY permanent password for the synthetic 3LO Cognito user ' +
        '(>=12 chars, upper/lower/number/symbol). Supplied at deploy time; not hardcoded.',
    });
    const testUserPassword =
      (this.node.tryGetContext('testUserPassword') as string) ||
      testUserPasswordParam.valueAsString;

    // Identity resolution mode. Default 'direct' (synthetic ARNs, no IDC calls) so the
    // stack deploys cleanly in any account. Set to 'idc' with your own store id to
    // resolve/expand real IAM Identity Center users & groups (read-only):
    //   cdk deploy -c identityMode=idc -c identityStoreId=d-xxxxxxxxxx
    const identityStoreId = (this.node.tryGetContext('identityStoreId') as string) || '';
    const identityMode =
      ((this.node.tryGetContext('identityMode') as string) || (identityStoreId ? 'idc' : 'direct'));

    // ===============================================================
    // 1. KMS customer-managed key (this stack's OWN key)
    // ===============================================================
    const cmk = new kms.Key(this, 'PreservationCmk', {
      alias: `${PREFIX}cmk`,
      description: 'TEST CMK for the standalone quick-legalhold-mcp WORM bucket + Firehose.',
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    cmk.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowAccountAdminManagement',
        principals: [new iam.AccountRootPrincipal()],
        actions: ['kms:*'],
        resources: ['*'],
      })
    );
    cmk.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowLogDeliveryService',
        principals: [new iam.ServicePrincipal('delivery.logs.amazonaws.com')],
        actions: ['kms:GenerateDataKey', 'kms:Decrypt'],
        resources: ['*'],
        conditions: {
          StringLike: { 'kms:EncryptionContext:SourceArn': `arn:aws:logs:${region}:${account}:*` },
        },
      })
    );

    // ===============================================================
    // 2. S3 WORM bucket (own): Object Lock GOVERNANCE, versioned, SSE-KMS,
    //    public access blocked, removable for TEST.
    // ===============================================================
    const wormBucket = new s3.Bucket(this, 'WormBucket', {
      bucketName: `${PREFIX}worm-${account}`,
      objectLockEnabled: true,
      objectLockDefaultRetention: s3.ObjectLockRetention.governance(cdk.Duration.days(1)),
      versioned: true,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: cmk,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      // WORM correctness: never auto-delete or destroy the preserved legal-hold
      // evidence. RETAIN the bucket on stack delete and do NOT attach the
      // auto-delete-objects custom resource (it delete-marked delivered records).
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ===============================================================
    // 3. DynamoDB held-user table (this stack's OWN table)
    // ===============================================================
    const usersTable = new ddb.Table(this, 'UsersTable', {
      tableName: `${PREFIX}users`,
      partitionKey: { name: 'user_arn', type: ddb.AttributeType.STRING },
      billingMode: ddb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ===============================================================
    // 4. Filter Lambda + Firehose delivery stream (own) -> WORM bucket
    // ===============================================================
    const filterFn = new lambda.Function(this, 'FilterFn', {
      functionName: `${PREFIX}filter`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'filter')),
      timeout: cdk.Duration.minutes(1),
      memorySize: 256,
      environment: { USERS_TABLE: usersTable.tableName },
    });
    usersTable.grantReadData(filterFn);

    const firehoseLogGroup = new logs.LogGroup(this, 'FirehoseLogGroup', {
      logGroupName: `/aws/kinesisfirehose/${PREFIX}stream-dp`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const firehoseLogStream = new logs.LogStream(this, 'FirehoseLogStream', {
      logGroup: firehoseLogGroup,
      logStreamName: 'S3Delivery',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const firehoseRole = new iam.Role(this, 'FirehoseRole', {
      roleName: `${PREFIX}firehose-role`,
      assumedBy: new iam.ServicePrincipal('firehose.amazonaws.com'),
    });
    wormBucket.grantReadWrite(firehoseRole);
    cmk.grantEncryptDecrypt(firehoseRole);
    filterFn.grantInvoke(firehoseRole);
    firehoseRole.addToPolicy(
      new iam.PolicyStatement({ actions: ['logs:PutLogEvents'], resources: [firehoseLogGroup.logGroupArn] })
    );

    const deliveryStream = new firehose.CfnDeliveryStream(this, 'DeliveryStreamDP', {
      deliveryStreamName: `${PREFIX}stream-dp`,
      deliveryStreamType: 'DirectPut',
      extendedS3DestinationConfiguration: {
        bucketArn: wormBucket.bucketArn,
        roleArn: firehoseRole.roleArn,
        // Group-first dynamic partitioning: the filter Lambda emits
        // metadata.partitionKeys.grp (group_name or _direct) and .usr (clean
        // UserName). Layout: chat-logs/<grp>/<usr>/YYYY/MM/DD/. e-discovery of a
        // group or a single custodian is then a ListObjects on their prefix.
        prefix: 'chat-logs/!{partitionKeyFromLambda:grp}/!{partitionKeyFromLambda:usr}/!{timestamp:yyyy/MM/dd}/',
        errorOutputPrefix: 'errors/!{firehose:error-output-type}/!{timestamp:yyyy/MM/dd}/',
        // Dynamic partitioning requires intervalInSeconds >= 60.
        bufferingHints: { intervalInSeconds: 60, sizeInMBs: 64 },
        compressionFormat: 'GZIP',
        encryptionConfiguration: { kmsEncryptionConfig: { awskmsKeyArn: cmk.keyArn } },
        dynamicPartitioningConfiguration: { enabled: true },
        cloudWatchLoggingOptions: {
          enabled: true,
          logGroupName: firehoseLogGroup.logGroupName,
          logStreamName: firehoseLogStream.logStreamName,
        },
        processingConfiguration: {
          enabled: true,
          processors: [
            {
              type: 'Lambda',
              parameters: [
                { parameterName: 'LambdaArn', parameterValue: filterFn.functionArn },
                { parameterName: 'BufferIntervalInSeconds', parameterValue: '60' },
                { parameterName: 'BufferSizeInMBs', parameterValue: '1' },
              ],
            },
          ],
        },
      },
    });
    deliveryStream.node.addDependency(firehoseRole);

    // ===============================================================
    // 5b. CHAT_LOGS -> our Firehose vended-log delivery wiring (ADOPT-not-OWN).
    //   The shared QuickSuite CHAT_LOGS delivery source is account-wide infra
    //   that also feeds /aws/quicksuite/chat and the observability S3 bucket.
    //   A Provider-backed custom resource idempotently ensures our own
    //   delivery-destination + a delivery binding source->our-destination, and
    //   on stack delete removes ONLY our delivery + destination (never the
    //   shared source, never the other deliveries).
    // ===============================================================
    const fhDestinationName = `${PREFIX}fh-destination`;
    const chatLogsSourceName = (this.node.tryGetContext('chatLogsSourceName') as string) || '';
    const manageSourceLifecycle =
      (this.node.tryGetContext('manageSourceLifecycle') as string) || 'false';
    const quickSightResourceArn = `arn:aws:quicksight:${region}:${account}:account/${account}`;

    const wiringFn = new lambda.Function(this, 'DeliveryWiringFn', {
      functionName: `${PREFIX}delivery-wiring`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'delivery_wiring')),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
    });
    // Least-privilege: only the vended-log delivery + enable actions we use.
    wiringFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'logs:DescribeDeliverySources',
          'logs:DescribeDeliveryDestinations',
          'logs:DescribeDeliveries',
          'logs:PutDeliverySource',
          'logs:PutDeliveryDestination',
          'logs:CreateDelivery',
          'logs:DeleteDelivery',
          'logs:DeleteDeliveryDestination',
        ],
        resources: ['*'], // these Describe/Put/Create/Delete-delivery* actions are not resource-scopable
      })
    );
    wiringFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['quicksight:AllowVendedLogDeliveryForResource'],
        resources: [quickSightResourceArn],
      })
    );

    const wiringProvider = new cr.Provider(this, 'DeliveryWiringProvider', {
      onEventHandler: wiringFn,
    });

    const deliveryWiring = new cdk.CustomResource(this, 'ChatLogsFirehoseWiring', {
      serviceToken: wiringProvider.serviceToken,
      properties: {
        FirehoseStreamArn: deliveryStream.attrArn, // referenced, not hardcoded
        DestinationName: fhDestinationName,
        ChatLogsSourceName: chatLogsSourceName,
        QuickSightResourceArn: quickSightResourceArn,
        ManageSourceLifecycle: manageSourceLifecycle,
      },
    });
    deliveryWiring.node.addDependency(deliveryStream);

    // ===============================================================
    // 6. Cognito (OWN): user pool + hosted-UI domain + resource server +
    //    3LO (authorization_code) client only — per-user auth, no M2M client.
    // ===============================================================
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `${PREFIX}pool`,
      selfSignUpEnabled: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      standardAttributes: { email: { required: false, mutable: true } },
    });

    const domainPrefix = `${PREFIX}${account}`.toLowerCase().replace(/[^a-z0-9-]/g, '-');
    const userPoolDomain = userPool.addDomain('UserPoolDomain', {
      cognitoDomain: { domainPrefix },
    });

    const resourceServerScope = new cognito.ResourceServerScope({
      scopeName: SCOPE_NAME,
      scopeDescription: 'Invoke the hold management MCP tools',
    });
    const resourceServer = userPool.addResourceServer('ResourceServer', {
      userPoolResourceServerName: RESOURCE_SERVER_ID,
      identifier: RESOURCE_SERVER_ID,
      scopes: [resourceServerScope],
    });

    // 3LO client (authorization_code) — L1 for exact control of flows/scopes/callbacks.
    // NOTE: the 2LO / M2M (client_credentials) client was intentionally removed —
    // this stack uses per-user 3LO auth only, so every hold is attributed to a
    // real logged-in Legal admin (chain-of-custody).
    const threeLoClient = new cognito.CfnUserPoolClient(this, 'ThreeLoClient', {
      userPoolId: userPool.userPoolId,
      clientName: 'quick-legalhold-mcp-3lo',
      generateSecret: true,
      allowedOAuthFlows: ['code'],
      allowedOAuthFlowsUserPoolClient: true,
      allowedOAuthScopes: ['openid', 'email', 'profile', FULL_SCOPE],
      supportedIdentityProviders: ['COGNITO'],
      callbackUrLs: [quickRedirectUri, LOCAL_CALLBACK],
      logoutUrLs: [quickRedirectUri, LOCAL_CALLBACK],
      explicitAuthFlows: ['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'],
    });
    threeLoClient.node.addDependency(resourceServer);
    threeLoClient.node.addDependency(userPoolDomain);

    const hostedUiBase = `https://${domainPrefix}.auth.${region}.amazoncognito.com`;
    const discoveryUrl =
      `https://cognito-idp.${region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`;

    // TEST-ONLY synthetic end-user, created directly in the Cognito pool with a
    // known, permanent password (no Secrets Manager). The password is a plain stack
    // value so it can be surfaced as a CloudFormation output for evaluation.
    const testUsername = 'legal-admin-test';
    const testEmail = 'legal-admin-test@example.com';

    // Create the user (idempotent physical id; deleted on stack teardown).
    const testUserCr = new cr.AwsCustomResource(this, 'CreateTestUser', {
      onCreate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminCreateUser',
        parameters: {
          UserPoolId: userPool.userPoolId,
          Username: testUsername,
          MessageAction: 'SUPPRESS',
          UserAttributes: [
            { Name: 'email', Value: testEmail },
            { Name: 'email_verified', Value: 'true' },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.of(`testuser-${testUsername}-${userPool.userPoolId}`),
      },
      onDelete: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminDeleteUser',
        parameters: { UserPoolId: userPool.userPoolId, Username: testUsername },
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:AdminCreateUser', 'cognito-idp:AdminDeleteUser', 'cognito-idp:AdminSetUserPassword'],
          resources: [userPool.userPoolArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    testUserCr.node.addDependency(userPool);

    // Set the known permanent password (so hosted-UI first login works with no
    // NEW_PASSWORD_REQUIRED challenge). Re-applied on update if the password changes.
    const setPw = new cr.AwsCustomResource(this, 'SetTestUserPassword', {
      onCreate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminSetUserPassword',
        parameters: {
          UserPoolId: userPool.userPoolId,
          Username: testUsername,
          Password: testUserPassword,
          Permanent: true,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`testuser-pw-${testUsername}-${userPool.userPoolId}`),
      },
      onUpdate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminSetUserPassword',
        parameters: {
          UserPoolId: userPool.userPoolId,
          Username: testUsername,
          Password: testUserPassword,
          Permanent: true,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`testuser-pw-${testUsername}-${userPool.userPoolId}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:AdminSetUserPassword'],
          resources: [userPool.userPoolArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    setPw.node.addDependency(testUserCr);

    // The 3LO app-client secret is generated by Cognito and is NOT exposed as a
    // native CloudFormation attribute. Fetch it via describeUserPoolClient so it
    // can be surfaced as a stack output alongside the other connection details.
    const clientSecretCr = new cr.AwsCustomResource(this, 'ThreeLoClientSecretReader', {
      onCreate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'describeUserPoolClient',
        parameters: { UserPoolId: userPool.userPoolId, ClientId: threeLoClient.ref },
        physicalResourceId: cr.PhysicalResourceId.of(`3lo-secret-${threeLoClient.ref}`),
      },
      onUpdate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'describeUserPoolClient',
        parameters: { UserPoolId: userPool.userPoolId, ClientId: threeLoClient.ref },
        physicalResourceId: cr.PhysicalResourceId.of(`3lo-secret-${threeLoClient.ref}`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:DescribeUserPoolClient'],
          resources: [userPool.userPoolArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    clientSecretCr.node.addDependency(threeLoClient);
    const threeLoClientSecret = clientSecretCr.getResponseField('UserPoolClient.ClientSecret');

    // ===============================================================
    // 5. Hold Manager MCP Lambda + interceptor + AgentCore Gateway
    //    pointed at THIS stack's own table.
    // ===============================================================
    const mcpFn = new lambda.Function(this, 'HoldManagerMcpFn', {
      functionName: `${PREFIX}hold-manager`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'hold_manager_mcp')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        USERS_TABLE: usersTable.tableName,
        IDENTITY_MODE: identityMode,
        IDENTITY_STORE_ID: identityStoreId,
      },
    });
    usersTable.grantReadWriteData(mcpFn);
    mcpFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'identitystore:ListUsers', 'identitystore:ListGroups', 'identitystore:GetGroupId',
          'identitystore:ListGroupMemberships', 'identitystore:DescribeUser', 'identitystore:DescribeGroup',
          'identitystore:GetUserId', 'identitystore:DescribeGroupMembership',
          'sso:DescribeInstance', 'sso:ListInstances',
        ],
        resources: ['*'],
      })
    );
    mcpFn.addPermission('AllowAgentCoreInvoke', {
      principal: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceAccount: account,
    });

    const interceptorFn = new lambda.Function(this, 'InterceptorFn', {
      functionName: `${PREFIX}interceptor`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'interceptor')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
    });
    interceptorFn.addPermission('AllowAgentCoreInvokeInterceptor', {
      principal: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceAccount: account,
    });

    const gatewayRole = new iam.Role(this, 'GatewayRole', {
      roleName: `${PREFIX}gateway-role`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [mcpFn.functionArn, interceptorFn.functionArn],
      })
    );

    const gateway = new cdk.CfnResource(this, 'McpGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: 'quick-legalhold-mcp-gw',
        Description: 'TEST: standalone MCP gateway for Amazon Quick legal-hold management tools.',
        ProtocolType: 'MCP',
        RoleArn: gatewayRole.roleArn,
        AuthorizerType: 'CUSTOM_JWT',
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: discoveryUrl,
            AllowedClients: [threeLoClient.ref],
          },
        },
        ProtocolConfiguration: {
          Mcp: {
            Instructions:
              'Legal-hold management tools: search identities, list group members, place/release holds, list holds.',
            SupportedVersions: ['2025-03-26'],
          },
        },
        InterceptorConfigurations: [
          {
            Interceptor: { Lambda: { Arn: interceptorFn.functionArn } },
            InterceptionPoints: ['REQUEST'],
            InputConfiguration: { PassRequestHeaders: true },
          },
        ],
      },
    });
    gateway.node.addDependency(gatewayRole);
    gateway.node.addDependency(threeLoClient);
    gateway.node.addDependency(threeLoClient);
    gateway.node.addDependency(interceptorFn);

    // Gateway Target — Lambda with inline MCP tool schema.
    const stringProp = (desc: string) => ({ Type: 'string', Description: desc });
    const toolDefs = [
      {
        Name: 'search_identities',
        Description: 'Search IAM Identity Center users and groups by a query string (IDC mode).',
        InputSchema: { Type: 'object', Properties: { query: stringProp('Case-insensitive substring to match user/group names.') }, Required: [] },
      },
      {
        Name: 'list_group_members',
        Description: 'List the member users of an IAM Identity Center group (IDC mode).',
        InputSchema: { Type: 'object', Properties: { group_name: stringProp('The group display name to expand.') }, Required: ['group_name'] },
      },
      {
        Name: 'place_hold',
        Description: 'Place an active legal hold for a user or (expanded) group. Idempotent: already-held users are left untouched with their original hold date preserved. Writes one item per resolved user_arn.',
        InputSchema: {
          Type: 'object',
          Properties: {
            type: stringProp('Target type: "user" or "group".'),
            name: stringProp('User name, group name, or a direct user_arn.'),
            matter_id: stringProp('Optional free-text legal matter identifier (defaults to "UNSPECIFIED").'),
            custodian: stringProp('Optional custodian name.'),
          },
          Required: ['type', 'name'],
        },
      },
      {
        Name: 'release_hold',
        Description: 'Release (set status=released) the holds matching a user or group.',
        InputSchema: {
          Type: 'object',
          Properties: { type: stringProp('Target type: "user" or "group".'), name: stringProp('User name, group name, or a direct user_arn.') },
          Required: ['type', 'name'],
        },
      },
      {
        Name: 'list_holds',
        Description: 'List all current legal-hold items.',
        InputSchema: { Type: 'object', Properties: {}, Required: [] },
      },
    ];

    const target = new cdk.CfnResource(this, 'McpGatewayTarget', {
      type: 'AWS::BedrockAgentCore::GatewayTarget',
      properties: {
        GatewayIdentifier: gateway.getAtt('GatewayIdentifier').toString(),
        Name: 'hold-manager-lambda-target',
        Description: 'Hold Manager Lambda exposed as MCP tools.',
        CredentialProviderConfigurations: [{ CredentialProviderType: 'GATEWAY_IAM_ROLE' }],
        TargetConfiguration: {
          Mcp: { Lambda: { LambdaArn: mcpFn.functionArn, ToolSchema: { InlinePayload: toolDefs } } },
        },
      },
    });
    target.node.addDependency(gateway);
    target.node.addDependency(mcpFn);

    // ===============================================================
    // Outputs
    // ===============================================================
    new cdk.CfnOutput(this, 'GatewayId', { value: gateway.getAtt('GatewayIdentifier').toString() });
    new cdk.CfnOutput(this, 'GatewayUrl', { value: gateway.getAtt('GatewayUrl').toString() });
    new cdk.CfnOutput(this, 'GatewayArn', { value: gateway.getAtt('GatewayArn').toString() });
    new cdk.CfnOutput(this, 'ToolLambdaName', { value: mcpFn.functionName });
    new cdk.CfnOutput(this, 'FilterLambdaName', { value: filterFn.functionName });
    new cdk.CfnOutput(this, 'FirehoseStreamName', { value: `${PREFIX}stream-dp` });
    new cdk.CfnOutput(this, 'WormBucketName', { value: wormBucket.bucketName });
    new cdk.CfnOutput(this, 'ChatLogsFhDestinationName', { value: fhDestinationName });
    new cdk.CfnOutput(this, 'ChatLogsDeliverySourceName', {
      value: deliveryWiring.getAttString('SourceName'),
    });
    new cdk.CfnOutput(this, 'ChatLogsDeliveryId', {
      value: deliveryWiring.getAttString('DeliveryId'),
    });
    new cdk.CfnOutput(this, 'CmkArn', { value: cmk.keyArn });
    new cdk.CfnOutput(this, 'UsersTableName', { value: usersTable.tableName });

    // Cognito outputs
    new cdk.CfnOutput(this, 'CognitoUserPoolId', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'CognitoDiscoveryUrl', { value: discoveryUrl });
    new cdk.CfnOutput(this, 'CognitoTokenUrl', { value: `${hostedUiBase}/oauth2/token` });
    new cdk.CfnOutput(this, 'CognitoScope', { value: FULL_SCOPE });

    // 3LO
    new cdk.CfnOutput(this, 'ThreeLoClientId', { value: threeLoClient.ref });
    new cdk.CfnOutput(this, 'ThreeLoClientSecret', {
      description: 'OAuth 2.0 confidential app-client secret for the MCP connector.',
      value: threeLoClientSecret,
    });
    new cdk.CfnOutput(this, 'ThreeLoAuthorizeUrl', { value: `${hostedUiBase}/oauth2/authorize` });
    new cdk.CfnOutput(this, 'ThreeLoTokenUrl', { value: `${hostedUiBase}/oauth2/token` });
    new cdk.CfnOutput(this, 'ThreeLoScopes', { value: 'openid email profile holds-api/invoke' });
    new cdk.CfnOutput(this, 'ThreeLoRedirectUri', { value: quickRedirectUri });
    new cdk.CfnOutput(this, 'ThreeLoLocalCallback', { value: LOCAL_CALLBACK });

    // --- TEST-ONLY synthetic user connection details (surfaced directly) ---
    // These are sample credentials for evaluation only. Never use them for real
    // identities. Remove the test user before any production use.
    new cdk.CfnOutput(this, 'ThreeLoTestUsername', { value: testUsername });
    new cdk.CfnOutput(this, 'ThreeLoTestUserPassword', {
      description: 'TEST-ONLY: permanent password for the synthetic 3LO test user.',
      value: testUserPassword,
    });
  }
}
