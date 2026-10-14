import { ITable } from "aws-cdk-lib/aws-dynamodb";
import {
  AwsCustomResource,
  AwsCustomResourcePolicy,
  AwsSdkCall,
  PhysicalResourceId,
} from "aws-cdk-lib/custom-resources";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";
import { TableMetadataConfig, validateTableRegistry } from "../common/table-registry-config";

export interface TableRegistryProps {
  readonly tableMetadataTable: ITable;
  readonly rows: readonly TableMetadataConfig[];
}

/** Writes the CDK table configuration into the DynamoDB table the workflow reads at runtime. */
export class TableRegistry extends Construct {
  public readonly rowResources: AwsCustomResource[];

  constructor(scope: Construct, id: string, props: TableRegistryProps) {
    super(scope, id);

    validateTableRegistry(props.rows);
    this.rowResources = props.rows.map((row) => this.createRow(props.tableMetadataTable, row));
  }

  private createRow(table: ITable, row: TableMetadataConfig): AwsCustomResource {
    const putItem: AwsSdkCall = {
      service: "DynamoDB",
      action: "putItem",
      parameters: {
        TableName: table.tableName,
        Item: {
          TableName: { S: row.tableName },
          Destination: { S: "STRUCTURED" },
          Enabled: { BOOL: row.enabled },
          Fields: { S: row.fields },
          BaseQuery: { S: row.baseQuery },
        },
      },
      physicalResourceId: PhysicalResourceId.of(row.tableName),
    };

    const resource = new AwsCustomResource(this, this.constructId(row.tableName), {
      onCreate: putItem,
      onUpdate: putItem,
      onDelete: {
        service: "DynamoDB",
        action: "deleteItem",
        parameters: {
          TableName: table.tableName,
          Key: { TableName: { S: row.tableName } },
        },
      },
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: [table.tableArn] }),
      installLatestAwsSdk: false,
    });
    resource.node.addDependency(table);

    NagSuppressions.addResourceSuppressions(
      resource,
      [
        {
          id: "AwsSolutions-IAM4",
          reason: "The CDK AwsCustomResource provider uses its managed logging policy.",
        },
        {
          id: "AwsSolutions-IAM5",
          reason:
            "The generated SDK policy is limited to PutItem and DeleteItem on the one registry table.",
        },
        {
          id: "AwsSolutions-L1",
          reason: "The AwsCustomResource provider runtime is managed by the CDK.",
        },
      ],
      true,
    );
    return resource;
  }

  private constructId(tableName: string): string {
    const pascalName = tableName
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join("");
    return `${pascalName}Metadata`;
  }
}
