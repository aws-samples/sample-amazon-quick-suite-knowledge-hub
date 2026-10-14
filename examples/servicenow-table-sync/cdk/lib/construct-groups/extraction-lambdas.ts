import { Duration } from "aws-cdk-lib";
import { ITable } from "aws-cdk-lib/aws-dynamodb";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import { Architecture, DockerImageCode, DockerImageFunction } from "aws-cdk-lib/aws-lambda";
import { ISecret } from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createConstructId, createResourceName } from "../common/config";
import { DataLake } from "./data-lake";

const LIST_TABLES_HANDLER = "list_tables_handler.handler";
const EXTRACT_TABLE_HANDLER = "extract_table_handler.handler";
const EXTRACT_DELETES_HANDLER = "extract_deletes_handler.handler";

const EXTRACT_MEMORY_MB = 1024;
const EXTRACT_TIMEOUT = Duration.minutes(15);
const LIST_TABLES_MEMORY_MB = 256;
const LIST_TABLES_TIMEOUT = Duration.seconds(60);

export interface ExtractionLambdasProps {
  readonly projectName: ProjectName;
  readonly backendImageDirectory: string;
  readonly ingestionCheckpointTable: ITable;
  readonly tableMetadataTable: ITable;
  readonly dataLake: DataLake;
  readonly serviceNowSecret: ISecret;
  readonly environment: Record<string, string>;
}

/** Lambda functions invoked by the ingestion workflow. */
export class ExtractionLambdas extends Construct {
  public readonly listTablesLambda: DockerImageFunction;
  public readonly extractTableLambda: DockerImageFunction;
  public readonly extractDeletesLambda: DockerImageFunction;

  constructor(scope: Construct, id: string, props: ExtractionLambdasProps) {
    super(scope, id);

    this.listTablesLambda = new DockerImageFunction(
      this,
      createConstructId(ResourceName.LIST_TABLES_LAMBDA),
      {
        functionName: createResourceName(props.projectName, ResourceName.LIST_TABLES_LAMBDA),
        code: DockerImageCode.fromImageAsset(props.backendImageDirectory, {
          cmd: [LIST_TABLES_HANDLER],
          platform: Platform.LINUX_ARM64,
        }),
        architecture: Architecture.ARM_64,
        memorySize: LIST_TABLES_MEMORY_MB,
        timeout: LIST_TABLES_TIMEOUT,
        environment: props.environment,
      },
    );
    props.tableMetadataTable.grantReadData(this.listTablesLambda);

    this.extractTableLambda = this.createExtractFunction(
      props,
      ResourceName.EXTRACT_TABLE_LAMBDA,
      EXTRACT_TABLE_HANDLER,
    );
    this.extractDeletesLambda = this.createExtractFunction(
      props,
      ResourceName.EXTRACT_DELETES_LAMBDA,
      EXTRACT_DELETES_HANDLER,
    );
  }

  private createExtractFunction(
    props: ExtractionLambdasProps,
    resourceName: ResourceName,
    handler: string,
  ): DockerImageFunction {
    const fn = new DockerImageFunction(this, createConstructId(resourceName), {
      functionName: createResourceName(props.projectName, resourceName),
      code: DockerImageCode.fromImageAsset(props.backendImageDirectory, {
        cmd: [handler],
        platform: Platform.LINUX_ARM64,
      }),
      architecture: Architecture.ARM_64,
      memorySize: EXTRACT_MEMORY_MB,
      timeout: EXTRACT_TIMEOUT,
      environment: props.environment,
    });

    props.ingestionCheckpointTable.grantReadWriteData(fn);
    props.dataLake.grantReadWrite(fn);
    props.serviceNowSecret.grantRead(fn);
    return fn;
  }
}
