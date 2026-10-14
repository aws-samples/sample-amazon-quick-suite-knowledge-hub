import { RemovalPolicy, Stack } from "aws-cdk-lib";
import { ISecret } from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import { ProjectName } from "../common/config";
import { TableMetadataConfig } from "../common/table-registry-config";
import { DataLake } from "./data-lake";
import { ExtractionLambdas } from "./extraction-lambdas";
import { IngestionWorkflow } from "./ingestion-workflow";
import { NormalizerJob } from "./normalizer-job";
import { StateTables } from "./state-tables";
import { TableRegistry } from "./table-registry";

/** The S3 Tables bucket and namespace the Glue job merges into. */
export interface PipelineMergeTarget {
  readonly s3TablesBucketArn: string;
  readonly s3TablesNamespace: string;
}

/**
 * The configuration each stack passes to {@link composeIngestionPipeline}: the merge
 * target, the ServiceNow secret, the table rows, and whether the schedule is enabled.
 */
export interface IngestionPipelineConfig {
  readonly projectName: ProjectName;
  readonly account: string;
  readonly region: string;
  readonly removalPolicy: RemovalPolicy;
  /** Imported by name for the solution stack, created for the test stack. */
  readonly serviceNowSecret: ISecret;
  readonly mergeTarget: PipelineMergeTarget;
  readonly scheduleEnabled: boolean;
  /** Rows written to the DynamoDB table list. */
  readonly registryRows: readonly TableMetadataConfig[];
}

/** The construct groups {@link composeIngestionPipeline} creates under the stack. */
export interface IngestionPipelineParts {
  readonly stateTables: StateTables;
  readonly dataLake: DataLake;
  readonly extractionLambdas: ExtractionLambdas;
  readonly tableRegistry: TableRegistry;
  readonly normalizerJob: NormalizerJob;
  readonly ingestionWorkflow: IngestionWorkflow;
}

/**
 * Composes the table sync pipeline shared by the solution stack and the test stack.
 * Each construct group is created directly under the passed `stack`, which keeps the
 * CloudFormation logical IDs stable. The simulator and secret seeder are added by the
 * test stack after this call.
 */
export function composeIngestionPipeline(
  stack: Stack,
  config: IngestionPipelineConfig,
): IngestionPipelineParts {
  const {
    projectName,
    account,
    region,
    removalPolicy,
    serviceNowSecret,
    mergeTarget,
    scheduleEnabled,
    registryRows,
  } = config;

  const backendImageDirectory = path.join(__dirname, "..", "..", "..", "backend");

  const stateTables = new StateTables(stack, "StateTables", { projectName, removalPolicy });

  const dataLake = new DataLake(stack, "DataLake", {
    projectName,
    removalPolicy,
    account,
    region,
  });

  // AWS_REGION is omitted because the Lambda runtime sets it.
  const backendEnvironment: Record<string, string> = {
    INGESTION_CHECKPOINT_TABLE_NAME: stateTables.ingestionCheckpointTable.tableName,
    TABLE_METADATA_TABLE_NAME: stateTables.tableMetadataTable.tableName,
    RAW_DATA_LAKE_BUCKET_NAME: dataLake.rawDataLakeBucket.bucketName,
    SERVICE_NOW_SECRET_ARN: serviceNowSecret.secretArn,
    AWS_REGION_HINT: region,
  };

  const extractionLambdas = new ExtractionLambdas(stack, "ExtractionLambdas", {
    projectName,
    backendImageDirectory,
    ingestionCheckpointTable: stateTables.ingestionCheckpointTable,
    tableMetadataTable: stateTables.tableMetadataTable,
    dataLake,
    serviceNowSecret,
    environment: backendEnvironment,
  });

  const tableRegistry = new TableRegistry(stack, "TableRegistry", {
    tableMetadataTable: stateTables.tableMetadataTable,
    rows: registryRows,
  });

  const normalizerJob = new NormalizerJob(stack, "NormalizerJob", {
    projectName,
    account,
    region,
    dataLake,
    s3TablesBucketArn: mergeTarget.s3TablesBucketArn,
    s3TablesNamespace: mergeTarget.s3TablesNamespace,
  });

  const ingestionWorkflow = new IngestionWorkflow(stack, "IngestionWorkflow", {
    projectName,
    removalPolicy,
    extractionLambdas,
    normalizerJob,
    scheduleEnabled,
  });

  return {
    stateTables,
    dataLake,
    extractionLambdas,
    tableRegistry,
    normalizerJob,
    ingestionWorkflow,
  };
}
