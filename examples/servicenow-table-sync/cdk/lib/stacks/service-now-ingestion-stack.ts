import { CfnOutput, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import { Secret } from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import {
  ProjectName,
  ResourceName,
  S3_TABLES_NAMESPACE,
  createConstructId,
  createResourceName,
} from "../common/config";
import { TABLE_REGISTRY } from "../common/table-registry-config";
import { DataLake } from "../construct-groups/data-lake";
import { ExtractionLambdas } from "../construct-groups/extraction-lambdas";
import { IngestionWorkflow } from "../construct-groups/ingestion-workflow";
import { composeIngestionPipeline } from "../construct-groups/ingestion-pipeline";
import { NormalizerJob } from "../construct-groups/normalizer-job";
import { StateTables } from "../construct-groups/state-tables";
import { TableBucket } from "../construct-groups/table-bucket";
import { TableRegistry } from "../construct-groups/table-registry";

/** Props for {@link ServiceNowIngestionStack}. */
export interface ServiceNowIngestionStackProps extends StackProps {
  readonly projectName: ProjectName;
  readonly account: string;
  readonly region: string;
  /** Whether the recurring schedule is enabled. A manual execution always works. */
  readonly scheduleEnabled: boolean;
}

/**
 * The ServiceNow table sync stack. It creates the S3 Tables bucket and namespace,
 * imports the fixed-name ServiceNow secret that you create before deploying, and
 * composes the pipeline through {@link composeIngestionPipeline}. Every resource uses
 * service-managed encryption.
 */
export class ServiceNowIngestionStack extends Stack {
  public readonly tableBucket: TableBucket;
  public readonly stateTables: StateTables;
  public readonly dataLake: DataLake;
  public readonly extractionLambdas: ExtractionLambdas;
  public readonly tableRegistry: TableRegistry;
  public readonly normalizerJob: NormalizerJob;
  public readonly ingestionWorkflow: IngestionWorkflow;

  constructor(scope: Construct, id: string, props: ServiceNowIngestionStackProps) {
    super(scope, id, props);

    const { projectName, account, region, scheduleEnabled } = props;
    const removalPolicy = RemovalPolicy.DESTROY;

    this.tableBucket = new TableBucket(this, "TableBucket", {
      projectName,
      account,
      region,
      namespace: S3_TABLES_NAMESPACE,
      removalPolicy,
    });

    // The stack never creates this secret. grantRead scopes GetSecretValue to its ARN.
    const serviceNowSecret = Secret.fromSecretNameV2(
      this,
      createConstructId(ResourceName.SERVICE_NOW_SECRET),
      createResourceName(projectName, ResourceName.SERVICE_NOW_SECRET),
    );

    const parts = composeIngestionPipeline(this, {
      projectName,
      account,
      region,
      removalPolicy,
      serviceNowSecret,
      mergeTarget: {
        s3TablesBucketArn: this.tableBucket.bucketArn,
        s3TablesNamespace: this.tableBucket.namespaceName,
      },
      scheduleEnabled,
      registryRows: TABLE_REGISTRY,
    });

    this.stateTables = parts.stateTables;
    this.dataLake = parts.dataLake;
    this.extractionLambdas = parts.extractionLambdas;
    this.tableRegistry = parts.tableRegistry;
    this.normalizerJob = parts.normalizerJob;
    this.ingestionWorkflow = parts.ingestionWorkflow;

    new CfnOutput(this, "TableBucketArn", {
      value: this.tableBucket.bucketArn,
      description: "S3 table bucket ARN to enter when you create the Amazon Quick data source.",
    });
    new CfnOutput(this, "StateMachineArn", {
      value: this.ingestionWorkflow.stateMachine.stateMachineArn,
      description: "State machine ARN for a manual first execution.",
    });
  }
}
