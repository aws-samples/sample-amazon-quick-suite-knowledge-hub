# Deploy the ServiceNow table sync and connect Amazon Quick

This runbook takes you from a ServiceNow instance with no integration to an Amazon Quick dataset that answers questions about your incidents. Work through it in order. ServiceNow comes first because the sync needs the OAuth credentials that you create there, and the AWS stack reads those credentials from [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/), so the secret must exist before you deploy.

Check the prerequisites in the [README](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/servicenow-table-sync/README.md#prerequisites) before you begin. Replace each value in angle brackets, such as `<INSTANCE>`, with your own.

## Set up ServiceNow

The sync authenticates to ServiceNow with the OAuth 2.0 client credentials grant. In this grant, the sync is both the client and the resource owner, so no person signs in and no password is stored. ServiceNow instead runs every request as an integration user that you choose, which means that user's access rules decide what the sync can read.

### Turn on the client credentials grant

ServiceNow added the client credentials grant in the Washington DC release, and an administrator must turn it on with a system property ([Up Your OAuth2.0 Game: Inbound Client Credentials with Washington DC](https://www.servicenow.com/community/developer-blog/up-your-oauth2-0-game-inbound-client-credentials-with-washington/ba-p/2816891)). Sign in to your instance as a user with the `admin` role.

**To confirm the required plugins**

1. In the filter navigator, enter `sys_plugins.list`, and then press Enter.
2. Confirm that OAuth 2.0 (`com.snc.platform.security.oauth`), REST API Provider (`com.glide.rest`), Authentication scope (`com.glide.auth.scope`), and REST API Auth Scope Plugin (`com.glide.rest.auth.scope`) are installed.
3. Install any plugin that is missing.

**To turn on the grant**

1. In the filter navigator, enter `sys_properties.list`, and then press Enter.
2. Check for an existing property named `glide.oauth.inbound.client.credential.grant_type.enabled`. If it exists, set **Value** to `true` and choose **Update**. Skip the remaining steps.
3. If it doesn't exist, choose **New**.
4. For **Name**, enter `glide.oauth.inbound.client.credential.grant_type.enabled`.
5. For **Type**, choose `true | false`.
6. For **Value**, enter `true`.
7. Choose **Submit**.

### Create the integration user

The integration user should be able to read the tables you sync and do nothing else.

**To create the user**

1. Go to **User Administration** > **Users**, and then choose **New**.
2. For **User ID**, enter `quick_table_sync`.
3. Set **Identity type** to **Machine**. On earlier ServiceNow releases that don't have this field, select **Web service access only** instead, which prevents anyone from using the account to sign in to the user interface ([Assigning Roles to API Users](https://www.servicenow.com/community/developer-forum/assigning-roles-to-api-users/m-p/3509994)).
4. Choose **Submit**.

### Grant read access to the tables

An OAuth token carries the access of the integration user, so the user needs a read access control rule (ACL) for every table in your sync, plus `sys_audit_delete`, the table where ServiceNow records deletions. You create a role, attach the read rules to it, and assign it to the user. ServiceNow requires the `security_admin` role to create ACLs, so [switch to that role](https://www.servicenow.com/community/itsm-forum/limit-users-to-update-incidents-based-on-assignment-group/td-p/3439461) before you start.

**To create the role**

1. Go to **User Administration** > **Roles**, and then choose **New**.
2. For **Name**, enter `quick_table_sync_reader`.
3. Choose **Submit**.

**To assign the role**

1. Go to **User Administration** > **Users**, and then open `quick_table_sync`.
2. In the **Roles** related list, choose **Edit**.
3. Add `quick_table_sync_reader`, and then choose **Save**.
4. Choose **Update**.

**To create the read rules for one table**

1. Go to **System Security** > **Access Control (ACL)**, and then choose **New**.
2. For **Type**, choose `record`.
3. For **Operation**, choose `read`.
4. For **Name**, choose the table, for example `incident`.
5. In the **Requires role** list, add `quick_table_sync_reader`.
6. Choose **Submit**.
7. Create another rule with the same settings, but for **Name** choose the table and the field `*`, so that the rule covers every field.

Repeat the rule procedure for each table in your sync and for `sys_audit_delete`. The default list is `incident`, `change_request`, `problem`, `sc_request`, `sc_req_item`, `task_sla`, `cmdb_ci_server`, and `cmdb_ci_appl`. The field-level rule matters because a user who can see a row but not its fields gets empty columns ([Grant table access to a service account in ServiceNow Catalog](https://learn.microsoft.com/en-us/microsoftsearch/granting-table-access-servicenow-catalog)).

### Create the OAuth application

The OAuth application is the record that holds the client ID and client secret and ties them to the integration user.

**To create the application**

1. Go to **System OAuth** > **Application Registry**, and then choose **New**.
2. Choose **Create an OAuth API endpoint for external clients**.
3. For **Name**, enter `Amazon Quick table sync`.
4. For **Default Grant Type**, choose **Client Credentials**.
5. Leave **Public Client** cleared. The client credentials grant is for confidential clients, and the redirect and login URLs aren't used.
6. For **OAuth Application User**, choose `quick_table_sync`.
7. Choose **Submit**.

If the form has no **OAuth Application User** field, open the form context menu, choose **Configure** > **Form Builder**, add the existing **OAuth Application User** field to the **Default** view, and save ([Up Your OAuth2.0 Game: Inbound Client Credentials with Washington DC](https://www.servicenow.com/community/developer-blog/up-your-oauth2-0-game-inbound-client-credentials-with-washington/ba-p/2816891)). Then open the application and set the field.

After you submit, open the application again and copy the **Client ID** and **Client Secret**. You need both in the next section.

The token you get has the full access of the integration user. ServiceNow can narrow it further with a REST API authentication scope, for example one that allows only `GET` requests to the Table API ([Limit the access granted to an OAuth client by using REST API authentication scopes](https://www.servicenow.com/community/developer-articles/limit-the-access-granted-to-an-oauth-client-by-using-rest-api/ta-p/3173651)). The sync sends only read requests, so a read-only scope is a good addition once the sync works.

### Test the credentials

Test the credentials before you involve AWS. A token request that fails here has a ServiceNow cause, and finding it takes minutes instead of a failed workflow run.

**To request a token and read each table**

1. In a terminal, set your instance URL and client ID:

    ```bash
    export SN_URL="https://<INSTANCE>.service-now.com"
    export SN_CLIENT_ID="<CLIENT_ID>"
    ```

2. Enter the client secret when prompted. The input isn't echoed, and the secret stays out of your shell history and the process list:

    ```bash
    printf "Client secret: "; read -r -s SN_CLIENT_SECRET; echo; export SN_CLIENT_SECRET
    ```

3. Request a token from the `oauth_token.do` endpoint. The script URL-encodes the secret, which matters when it contains characters such as `&` or `+`:

    ```bash
    SN_TOKEN=$(python3 - <<'EOF'
    import json, os, urllib.parse, urllib.request
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["SN_CLIENT_ID"],
        "client_secret": os.environ["SN_CLIENT_SECRET"],
    }).encode()
    request = urllib.request.Request(os.environ["SN_URL"] + "/oauth_token.do", data=body)
    with urllib.request.urlopen(request) as response:
        print(json.load(response)["access_token"])
    EOF
    )
    ```

    The command prints nothing on success. An `HTTP Error 401` or `400` means ServiceNow refused the request. See [Troubleshooting](#troubleshooting).

4. Read one record from each table, including `sys_audit_delete`:

    ```bash
    for table in incident change_request problem sc_request sc_req_item task_sla \
                 cmdb_ci_server cmdb_ci_appl sys_audit_delete; do
      curl -s -o /dev/null -w "$table %{http_code}\n" \
        -H "Authorization: Bearer $SN_TOKEN" \
        "$SN_URL/api/now/table/$table?sysparm_limit=1"
    done
    ```

Every line should end in `200`. A `403` means the integration user can't read that table yet.

### Check that ServiceNow records deletions

The sync finds deleted records by reading `sys_audit_delete`, so a table must audit its deletions. ServiceNow audits record deletions by default, unless the table has the `no_audit_delete` dictionary attribute, and it doesn't audit deletions from system tables that start with `sys` unless you add them to a list ([Deleted records table](https://www.servicenow.com/community/now-platform-forum/deleted-records-table/td-p/1054869)). None of the default tables is a system table. If you add a table that has `no_audit_delete`, or a system table, its deleted records stay in Amazon Quick until you remove them yourself.

## Create the AWS secret

The stack reads the credentials from a Secrets Manager secret named `ServiceNowIngestionServiceNowCredentials`. It never creates the secret, so the credentials never pass through the CDK code or a CloudFormation template. Create the secret in the Region where you deploy.

The secret value is a JSON object with these keys. The sync rejects a secret that has any other key.

```json
{
  "instance_url": "https://<INSTANCE>.service-now.com",
  "client_id": "<CLIENT_ID>",
  "client_secret": "<CLIENT_SECRET>",
  "grant_type": "client_credentials"
}
```

**To create the secret (AWS CLI)**

1. Write the JSON to a file that only you can read, using the variables from the previous section:

    ```bash
    (umask 077; python3 -c 'import json,os; print(json.dumps({"instance_url": os.environ["SN_URL"], "client_id": os.environ["SN_CLIENT_ID"], "client_secret": os.environ["SN_CLIENT_SECRET"], "grant_type": "client_credentials"}))' > /tmp/sn-secret.json)
    ```

2. Create the secret:

    ```bash
    aws secretsmanager create-secret \
      --name ServiceNowIngestionServiceNowCredentials \
      --secret-string file:///tmp/sn-secret.json
    ```

3. Delete the file and clear the variables:

    ```bash
    rm -f /tmp/sn-secret.json; unset SN_CLIENT_SECRET SN_TOKEN
    ```

**To create the secret (console)**

1. Open the [Secrets Manager console](https://console.aws.amazon.com/secretsmanager/).
2. Choose **Store a new secret**.
3. For **Secret type**, choose **Other type of secret**.
4. Under **Key/value pairs**, add each key and value from the preceding JSON.
5. For **Encryption key**, leave `aws/secretsmanager`, and then choose **Next**.
6. For **Secret name**, enter `ServiceNowIngestionServiceNowCredentials`, and then choose **Next**.
7. Choose **Next**, and then choose **Store**.

When ServiceNow rotates the client secret, update the secret value and keep the same name.

## Choose the tables

Open [`cdk/lib/common/table-registry-config.ts`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/servicenow-table-sync/cdk/lib/common/table-registry-config.ts). The `TABLE_REGISTRY` list holds a `syncTable` entry for each ServiceNow table. The defaults are the standard tables for IT Service Management, the Service Catalog, and the CMDB, so you can deploy without a change.

To sync more or fewer tables, add or remove entries, and run `make deploy` again. Each entry takes optional settings. `fields` is a comma-separated list of columns, and an empty string syncs every column. `baseQuery` is a ServiceNow encoded query that filters the rows, such as `active=true`.

Each table is extracted in a single [AWS Lambda](https://aws.amazon.com/lambda/) invocation, which has a timeout. The first run reads every record that matches, so a table with a very large history can exceed it. To keep the first run short, set a `baseQuery` such as `sys_created_on>=2025-01-01`, which limits the history that the sync loads.

Before you deploy a new table, give the integration user the read rules from [Grant read access to the tables](#grant-read-access-to-the-tables), and test the table with the loop in [Test the credentials](#test-the-credentials).

## Deploy the stack

The stack deploys to the Region and account of your active AWS CLI profile. Your AWS identity needs to be able to create the resources in the stack, including IAM roles.

**To deploy**

1. From the repository root, select the Node.js version and install the dependencies:

    ```bash
    nvm use
    make install
    ```

2. Bootstrap the account and Region for the AWS CDK. You do this once for each account and Region ([Bootstrap your environment for use with the AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-env.html)):

    ```bash
    cd cdk
    npx cdk bootstrap
    cd ..
    ```

3. If you use Finch instead of Docker, point the CDK at it ([Build and deploy container image assets in CDK apps](https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html)):

    ```bash
    export CDK_DOCKER="$(which finch)"
    ```

4. Deploy:

    ```bash
    make deploy
    ```

The deploy builds the Lambda container image, so the first deploy takes longer than later ones. When it finishes, the CDK prints the outputs `TableBucketArn` and `StateMachineArn`. `TableBucketArn` is the value you enter in Amazon Quick, and `StateMachineArn` starts a sync.

From now on the workflow runs on the interval that `SYNC_INTERVAL_HOURS` sets in [`cdk/lib/common/config.ts`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/servicenow-table-sync/cdk/lib/common/config.ts). To turn the schedule off, deploy with `make deploy CDK_ARGS="--context enableSchedule=false"`, and start each sync yourself.

## Run the first sync

Start the first run yourself so that you don't wait for the schedule, and so that you can confirm the tables before you connect Amazon Quick.

**To start and check a sync**

1. Read the state machine ARN from the stack outputs:

    ```bash
    aws cloudformation describe-stacks --stack-name ServiceNowIngestionStack \
      --query "Stacks[0].Outputs"
    ```

2. Start an execution with the `StateMachineArn` value:

    ```bash
    aws stepfunctions start-execution --state-machine-arn <STATE_MACHINE_ARN>
    ```

3. Check the status with the `executionArn` that the previous command returned:

    ```bash
    aws stepfunctions describe-execution --execution-arn <EXECUTION_ARN> \
      --query status --output text
    ```

    The status changes from `RUNNING` to `SUCCEEDED`. You can also watch the execution in the [AWS Step Functions](https://aws.amazon.com/step-functions/) console.

4. List the tables that the run created, with the `TableBucketArn` value:

    ```bash
    aws s3tables list-tables --table-bucket-arn <TABLE_BUCKET_ARN> --namespace servicenow
    ```

You see a table for each ServiceNow table that returned records. A table with no matching records doesn't appear until it has data. If the status is `FAILED`, see [Troubleshooting](#troubleshooting).

## Set up Amazon Quick

Amazon Quick reads the tables straight from the table bucket, so there is no import job to build. The datasets in these steps use Direct Query, which reads the table when a visual loads instead of importing a copy, so a dataset has no refresh to schedule and new rows appear after the next sync ([Introducing new data source with S3 Tables in Amazon Quick](https://aws.amazon.com/blogs/machine-learning/from-data-lake-to-ai-ready-analytics-introducing-direct-query-with-s3-tables-in-amazon-quick/)). You can switch a dataset to SPICE later.

The table bucket uses Amazon S3 managed encryption, so you don't grant Amazon Quick access to an AWS KMS key. S3 Tables asks for a key permission only when a table uses SSE-KMS ([Permission requirements for S3 Tables SSE-KMS encryption](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-kms-permissions.html)).

### Allow Amazon Quick to read the table bucket

An administrator does this step once ([Creating a dataset using Amazon S3 Tables](https://docs.aws.amazon.com/quick/latest/userguide/create-a-data-set-s3-tables.html)). Sign in to Amazon Quick as an administrator, in the same Region as the table bucket.

**To allow access**

1. Choose your account name in the top-right corner, and then choose **Manage account**.
2. Under **Permissions**, choose **AWS Resources**.
3. Under **Allow access and auto discovery for these resources**, select **Amazon S3 Tables**.
4. Choose **Select S3 table buckets**.
5. Select the bucket named `service-now-ingestion-normalized-<ACCOUNT_ID>-<REGION>`, and then choose **Finish**.
6. Choose **Save**.

Amazon Quick creates an IAM role named `aws-quicksight-s3-tables-role-v0` with read access to the selected bucket. The AWS documentation labels this page **Manage QuickSuite**.

### Create the data source

**To create the data source**

1. Choose **Data** at left, and then open the **Data sources** tab.
2. Choose **Create data source**.
3. Choose the **Amazon S3 Tables** card.
4. For the name, enter `ServiceNow tables`.
5. For the table bucket ARN, enter the `TableBucketArn` value.
6. Choose **Create data source**.

### Create a dataset

Amazon Quick Sight is the business intelligence feature of Amazon Quick. You connect it to data sources, prepare data, build interactive dashboards, and ask natural language questions against your data. You create a dataset for each table you want to analyze, and the `incident` table is a good first one.

**To create a dataset**

1. Choose **Data** at left, and then open the **Datasets** tab.
2. Choose **Create dataset**.
3. Select the `ServiceNow tables` data source.
4. Select the `servicenow` namespace, and then select the `incident` table.
5. Choose **Direct Query**.
6. Choose **Edit/Preview data**.
7. Prepare the fields as described in the next procedure, and then choose **Save and publish**.

To add another table to the same dataset, choose **Add data** in the editor, select the data source, select the table, and join the tables on a column whose values match in both. Reference fields hold display values, such as a person's name, so a `sys_id` from one table doesn't match a reference in another.

### Prepare the fields

Every column arrives as a string, because the sync keeps all ServiceNow tables in the same format. Amazon Quick Sight assigns the data type when you prepare the dataset, so change the columns that hold dates and numbers. A row whose value doesn't fit the new type is skipped, so check the preview after each change ([Changing a field data type](https://docs.aws.amazon.com/quick/latest/userguide/changing-a-field-data-type.html)).

**To change a field data type**

1. In the data preview pane, find the field, for example `opened_at` or `sys_updated_on`.
2. Choose the data type icon under the field name.
3. Choose **Date**, **Integer**, or **Decimal**.
4. For a date, enter the format that your preview rows use, for example `yyyy-MM-dd HH:mm:ss` for a value such as `2026-09-01 10:00:00`.

Look at a few rows in the preview before you choose a format, because the values are the display values that ServiceNow returned.

### Share the dataset

**To share a dataset**

1. Choose **Data** at left, and then select the dataset that you want to share.
2. Choose the **Permissions** tab, and then choose **Add users & groups**.
3. Enter a user or group, and then choose **Add**.
4. For **Permissions**, choose **User** to let the person build analyses, or **Owner** to let them also edit and reshare the dataset.

Sharing a dataset gives people access to its data, and ServiceNow access rules don't carry over, so share it only with people who may read that data in ServiceNow ([Sharing datasets](https://docs.aws.amazon.com/quick/latest/userguide/sharing-data-sets.html)).

### Ask questions with Dataset Q&A

Dataset Q&A answers natural-language questions by reading the dataset's schema at query time, so you don't build a topic or define field mappings first ([How the Dataset Q&A feature of Amazon Quick powers the next generation of data decisions](https://aws.amazon.com/blogs/machine-learning/beyond-bi-how-the-dataset-qa-feature-of-amazon-quick-powers-the-next-generation-of-data-decisions/)). The column names are the ServiceNow field names, such as `short_description` and `assignment_group`.

**To ask a question in chat**

1. Choose **Chat agents** at left, and then select **My Assistant**.
2. Choose **Chat**.
3. Under **All data and apps**, choose **Add**, and then choose **Datasets**.
4. Select the dataset, and then choose **Save**.
5. Ask a question, for example "How many incidents were opened last month by priority?"

To give a team the same dataset, create a space and add the dataset as an asset. A space organizes documents and Amazon Quick resources, such as datasets, into a collection that agents use as their source of information ([Spaces](https://docs.aws.amazon.com/quick/latest/userguide/spaces-desktop.html)).

### Switch a dataset to SPICE

Direct Query has a time limit. Amazon Quick Sight stops generating a visual after two minutes ([Data source quotas](https://docs.aws.amazon.com/quick/latest/userguide/data-source-limits.html)), and a large table or a join across tables can reach that limit. SPICE imports a copy of the data and refreshes it on a schedule, which suits a dataset that is slow in Direct Query ([Introducing new data source with S3 Tables in Amazon Quick](https://aws.amazon.com/blogs/machine-learning/from-data-lake-to-ai-ready-analytics-introducing-direct-query-with-s3-tables-in-amazon-quick/)).

**To switch a dataset to SPICE**

1. Open **Data**, and then select the dataset that you want to change.
2. Choose **Edit dataset**.
3. In the top-right corner, change the query mode from **Direct Query** to **SPICE**.
4. Choose **Save and publish**.

## Change the table list later

To add a table, give the integration user the read rules for it, test it, add a `syncTable` line, and run `make deploy`. The table appears in the table bucket after the next sync. Then create a dataset for it in Amazon Quick.

To stop syncing a table, remove its line and run `make deploy`. The sync stops reading that table, but its table in the bucket and the datasets built on it stay, so delete them when you no longer need them.

## Troubleshooting

Check these in order. The sync logs each Lambda function to Amazon CloudWatch Logs under `/aws/lambda/ServiceNowIngestion<Name>Lambda`, and the Glue job under `/aws-glue/jobs/`. The Step Functions execution history shows which state failed.

### The token request fails with "integration user is not configured for OAuth"

ServiceNow returns `unauthorized_client` with this message when the OAuth application has no integration user ([OAuth 2.0 - Access Token](https://www.servicenow.com/community/servicenow-ide-sdk-and-fluent/oauth-2-0-access-token/m-p/3549561)). Set **OAuth Application User** on the application. If the error persists, confirm that `glide.oauth.inbound.client.credential.grant_type.enabled` is `true` and that **Default Grant Type** is **Client Credentials**.

### A table returns 403

The integration user can't read the table through the REST API. Check, in order, that the read rule for the table lists `quick_table_sync_reader`, that the user has that role, and that the table allows access through web services ([Failed API Level ACL Validation Error When Accessing Tables via REST API](https://hi.service-now.com/kb_view.do?sysparm_article=KB2686027)). Some instances also activate an access rule that requires the `snc_platform_rest_api_access` role for the Table API, so add that role to the user if every table returns 403 ([Assigning Roles to API Users](https://www.servicenow.com/community/developer-forum/assigning-roles-to-api-users/m-p/3509994)). A REST API access policy can also block the call ([REST API Access Policies: A Complete Guide](https://www.servicenow.com/community/platform-privacy-security-blog/demystifying-rest-api-access-policies-what-you-need-to-know-part/bc-p/3508776)).

### Rows arrive with empty columns

The integration user can read the rows but not the fields. Create the field-level read rule, with the field `*`, for that table.

### The execution fails in ExtractTables

Open the Lambda log group for `ExtractTableLambda`. An `extraction failed for <table>` message names the table and the reason. A timeout on a large table means the first run is too big, so add a `baseQuery` as described in [Choose the tables](#choose-the-tables). A failed table doesn't advance its checkpoint, so the next run reads the same window again and doesn't skip or duplicate records.

### The execution fails in NormalizeTables

Open the job run in the [AWS Glue](https://aws.amazon.com/glue/) console or the `/aws-glue/jobs/` log group. The merge into S3 Tables is idempotent, so you can start a new execution after you fix the cause.

### Amazon Quick doesn't list the table bucket

Confirm that your Amazon Quick account and the table bucket are in the same Region, that **Amazon S3 Tables** is selected on the **AWS Resources** page, and that you chose the bucket there. The page lists only the buckets in the current Region.

### A column I added is missing from the table

The Glue job creates each table from the columns of its first batch and doesn't add columns later. After you change a table's `fields` setting, or add a custom field in ServiceNow that you want, reload that table. Deleting the checkpoint is what makes the next run read the full history again.

**To reload a table**

1. Delete the table from the table bucket:

    ```bash
    aws s3tables delete-table --table-bucket-arn <TABLE_BUCKET_ARN> \
      --namespace servicenow --name <TABLE_NAME>
    ```

2. Delete the table's checkpoint row:

    ```bash
    aws dynamodb delete-item --table-name ServiceNowIngestionIngestionCheckpointTable \
      --key '{"TableName": {"S": "<TABLE_NAME>"}}'
    ```

3. Start an execution, as in [Run the first sync](#run-the-first-sync).

### A deleted ServiceNow record is still in the dataset

Confirm that the table audits deletions, as described in [Check that ServiceNow records deletions](#check-that-servicenow-records-deletions), and that the integration user can read `sys_audit_delete`. The sync applies deletions at the end of each run.

## Clean up resources

S3 Tables doesn't delete a table bucket that still holds tables, and the sync creates those tables, so delete them before you destroy the stack ([Deleting a table bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-buckets-delete.html)). Deleting a table is permanent.

**To remove the sync**

1. In Amazon Quick, delete the datasets and the `ServiceNow tables` data source that use the table bucket.
2. Delete each table in the `servicenow` namespace. List them first with `aws s3tables list-tables`, as in [Run the first sync](#run-the-first-sync), and then run this command for each table:

    ```bash
    aws s3tables delete-table --table-bucket-arn <TABLE_BUCKET_ARN> \
      --namespace servicenow --name <TABLE_NAME>
    ```

3. Destroy the stack:

    ```bash
    cd cdk
    npx cdk destroy ServiceNowIngestionStack
    ```

4. Delete the secret that you created:

    ```bash
    aws secretsmanager delete-secret --secret-id ServiceNowIngestionServiceNowCredentials
    ```

5. In ServiceNow, delete the OAuth application, the `quick_table_sync` user, the `quick_table_sync_reader` role and its read rules, and, if you no longer need the grant, set `glide.oauth.inbound.client.credential.grant_type.enabled` to `false`.

Secrets Manager keeps a deleted secret for a recovery period before it removes it. The stack doesn't own the secret, so `cdk destroy` leaves it in place.
