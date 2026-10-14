import { RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";
import { ProjectName, S3_TABLES_NAMESPACE } from "../common/config";
import { TEST_TABLE_REGISTRY } from "../common/table-registry-config";
import { DataLake } from "../construct-groups/data-lake";
import { ExtractionLambdas } from "../construct-groups/extraction-lambdas";
import { IngestionWorkflow } from "../construct-groups/ingestion-workflow";
import { composeIngestionPipeline } from "../construct-groups/ingestion-pipeline";
import { NormalizerJob } from "../construct-groups/normalizer-job";
import { Secrets } from "../construct-groups/secrets";
import { SimulatorTestSupport } from "../construct-groups/simulator-test-support";
import { StateTables } from "../construct-groups/state-tables";
import { TableBucket } from "../construct-groups/table-bucket";
import { TableRegistry } from "../construct-groups/table-registry";

/** Props for {@link ServiceNowIngestionTestStack}. */
export interface ServiceNowIngestionTestStackProps extends StackProps {
  readonly projectName: ProjectName;
  readonly account: string;
  readonly region: string;
  /** Directory holding the simulator Dockerfile. */
  readonly simulatorImageDirectory: string;
}

/**
 * An optional, disposable stack that runs the same pipeline against a ServiceNow
 * simulator with synthetic data, so you can try the sync without a ServiceNow instance.
 * It creates its own secret, S3 Tables bucket, and simulator API, and its schedule is
 * always off. The app includes it only when you pass `--context enableTestStack=true`.
 */
export class ServiceNowIngestionTestStack extends Stack {
  public readonly secrets: Secrets;
  public readonly tableBucket: TableBucket;
  public readonly stateTables: StateTables;
  public readonly dataLake: DataLake;
  public readonly extractionLambdas: ExtractionLambdas;
  public readonly tableRegistry: TableRegistry;
  public readonly normalizerJob: NormalizerJob;
  public readonly ingestionWorkflow: IngestionWorkflow;
  public readonly simulatorTestSupport: SimulatorTestSupport;

  constructor(scope: Construct, id: string, props: ServiceNowIngestionTestStackProps) {
    super(scope, id, props);

    const { projectName, account, region, simulatorImageDirectory } = props;
    const removalPolicy = RemovalPolicy.DESTROY;

    this.secrets = new Secrets(this, "Secrets", { projectName, removalPolicy });

    this.tableBucket = new TableBucket(this, "TableBucket", {
      projectName,
      account,
      region,
      namespace: S3_TABLES_NAMESPACE,
      removalPolicy,
    });

    const parts = composeIngestionPipeline(this, {
      projectName,
      account,
      region,
      removalPolicy,
      serviceNowSecret: this.secrets.serviceNowSecret,
      mergeTarget: {
        s3TablesBucketArn: this.tableBucket.bucketArn,
        s3TablesNamespace: this.tableBucket.namespaceName,
      },
      scheduleEnabled: false,
      registryRows: TEST_TABLE_REGISTRY,
    });

    this.stateTables = parts.stateTables;
    this.dataLake = parts.dataLake;
    this.extractionLambdas = parts.extractionLambdas;
    this.tableRegistry = parts.tableRegistry;
    this.normalizerJob = parts.normalizerJob;
    this.ingestionWorkflow = parts.ingestionWorkflow;

    this.simulatorTestSupport = new SimulatorTestSupport(this, "SimulatorTestSupport", {
      projectName,
      simulatorImageDirectory,
      serviceNowSecret: this.secrets.serviceNowSecret,
    });
  }
}
