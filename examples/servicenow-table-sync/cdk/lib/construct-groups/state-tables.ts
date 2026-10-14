import { RemovalPolicy } from "aws-cdk-lib";
import { AttributeType, BillingMode, Table, TableEncryption } from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createConstructId, createResourceName } from "../common/config";

/** Configuration for the {@link StateTables} construct group. */
export interface StateTablesProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
}

/**
 * The two ingestion state tables, both keyed on `TableName`, billed on demand, with
 * point-in-time recovery and the AWS managed key for DynamoDB.
 *
 * - `ingestionCheckpointTable`: one row per extracted ServiceNow table holding the
 *   `sys_updated_on` high-water mark and last-run status.
 * - `tableMetadataTable`: the table list the Step Functions Map state fans out over.
 */
export class StateTables extends Construct {
  public readonly ingestionCheckpointTable: Table;
  public readonly tableMetadataTable: Table;

  constructor(scope: Construct, id: string, props: StateTablesProps) {
    super(scope, id);

    const { projectName, removalPolicy } = props;

    const shared = {
      billingMode: BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: TableEncryption.AWS_MANAGED,
      partitionKey: { name: "TableName", type: AttributeType.STRING },
      removalPolicy,
    } as const;

    this.ingestionCheckpointTable = new Table(
      this,
      createConstructId(ResourceName.INGESTION_CHECKPOINT_TABLE),
      {
        ...shared,
        tableName: createResourceName(projectName, ResourceName.INGESTION_CHECKPOINT_TABLE),
      },
    );

    this.tableMetadataTable = new Table(
      this,
      createConstructId(ResourceName.TABLE_METADATA_TABLE),
      {
        ...shared,
        tableName: createResourceName(projectName, ResourceName.TABLE_METADATA_TABLE),
      },
    );
  }
}
