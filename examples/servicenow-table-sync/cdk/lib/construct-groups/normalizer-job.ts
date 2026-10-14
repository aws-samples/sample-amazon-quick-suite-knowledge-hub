import { Stack } from "aws-cdk-lib";
import { CfnJob } from "aws-cdk-lib/aws-glue";
import { Effect, PolicyStatement, Role, ServicePrincipal } from "aws-cdk-lib/aws-iam";
import { Asset } from "aws-cdk-lib/aws-s3-assets";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";
import * as path from "path";
import { ProjectName, ResourceName, createConstructId, createResourceName } from "../common/config";
import { DataLake } from "./data-lake";

/** Glue 5.0 is required for S3 Tables; Apache Iceberg is bundled. */
const GLUE_VERSION = "5.0";
const GLUE_WORKER_TYPE = "G.1X";
const GLUE_WORKER_COUNT = 2;

/** Configuration for the {@link NormalizerJob} construct group. */
export interface NormalizerJobProps {
  readonly projectName: ProjectName;
  readonly account: string;
  readonly region: string;
  readonly dataLake: DataLake;
  /** The S3 Tables (Iceberg) table bucket ARN the merged data lands in. */
  readonly s3TablesBucketArn: string;
  /** The Iceberg namespace (database) the per-table Iceberg tables live under. */
  readonly s3TablesNamespace: string;
}

/**
 * The per-table AWS Glue job that merges one table's staged Parquet into its Iceberg
 * table. The workflow starts one run per table. The S3 Tables catalog is wired through
 * the job's `--conf` default arguments, so the script needs no connection details. Job
 * bookmarks are on, so only new staged files are read.
 *
 * The job role can read and write the staging bucket (to read staged Parquet and delete
 * it after the merge) and has the S3 Tables and Glue Data Catalog access the Iceberg
 * catalog needs.
 */
export class NormalizerJob extends Construct {
  public readonly job: CfnJob;
  public readonly jobName: string;
  public readonly role: Role;

  constructor(scope: Construct, id: string, props: NormalizerJobProps) {
    super(scope, id);

    const { projectName, account, region, dataLake, s3TablesBucketArn, s3TablesNamespace } = props;

    this.role = new Role(this, createConstructId(ResourceName.NORMALIZER_ROLE), {
      roleName: createResourceName(projectName, ResourceName.NORMALIZER_ROLE),
      assumedBy: new ServicePrincipal("glue.amazonaws.com"),
    });
    dataLake.grantReadWrite(this.role);
    this.grantS3Tables(s3TablesBucketArn);
    this.grantGlueCatalog(account, region);
    this.grantContinuousLogging(account, region);

    const script = new Asset(this, createConstructId("NormalizerScript"), {
      path: path.join(
        __dirname,
        "..",
        "..",
        "..",
        "backend",
        "src",
        "glue",
        "normalize_content_job.py",
      ),
    });
    script.grantRead(this.role);

    this.jobName = createResourceName(projectName, ResourceName.NORMALIZER_JOB);
    this.job = new CfnJob(this, createConstructId(ResourceName.NORMALIZER_JOB), {
      name: this.jobName,
      role: this.role.roleArn,
      glueVersion: GLUE_VERSION,
      workerType: GLUE_WORKER_TYPE,
      numberOfWorkers: GLUE_WORKER_COUNT,
      command: {
        name: "glueetl",
        pythonVersion: "3",
        scriptLocation: script.s3ObjectUrl,
      },
      // The Step Functions task supplies the per-run argument (--table_name).
      defaultArguments: {
        "--job-language": "python",
        "--datalake-formats": "iceberg",
        "--job-bookmark-option": "job-bookmark-enable",
        "--enable-metrics": "true",
        "--raw_data_lake_bucket": dataLake.rawDataLakeBucket.bucketName,
        "--s3_tables_namespace": s3TablesNamespace,
        "--conf": this.icebergConf(s3TablesBucketArn),
      },
    });

    this.suppressNags();
  }

  private icebergConf(s3TablesBucketArn: string): string {
    const catalog = "spark_catalog";
    return [
      "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
      `spark.sql.catalog.${catalog}=org.apache.iceberg.spark.SparkSessionCatalog`,
      `spark.sql.catalog.${catalog}.catalog-impl=software.amazon.s3tables.iceberg.S3TablesCatalog`,
      `spark.sql.catalog.${catalog}.warehouse=${s3TablesBucketArn}`,
      `spark.sql.catalog.${catalog}.client.region=${Stack.of(this).region}`,
    ].join(" --conf ");
  }

  private grantS3Tables(s3TablesBucketArn: string): void {
    this.role.addToPolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          "s3tables:GetTableBucket",
          "s3tables:GetNamespace",
          "s3tables:ListNamespaces",
          "s3tables:CreateNamespace",
          "s3tables:GetTable",
          "s3tables:ListTables",
          "s3tables:CreateTable",
          "s3tables:GetTableData",
          "s3tables:PutTableData",
          "s3tables:GetTableMetadataLocation",
          "s3tables:UpdateTableMetadataLocation",
        ],
        resources: [s3TablesBucketArn, `${s3TablesBucketArn}/*`],
      }),
    );
  }

  private grantGlueCatalog(account: string, region: string): void {
    this.role.addToPolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
        ],
        resources: [
          `arn:aws:glue:${region}:${account}:catalog`,
          `arn:aws:glue:${region}:${account}:database/*`,
          `arn:aws:glue:${region}:${account}:table/*`,
        ],
      }),
    );
  }

  /**
   * Grants the job role CloudWatch Logs access for continuous logging, scoped to this
   * account and Region's `/aws-glue/jobs/*` log groups and their streams. Without it the
   * run fails at startup with a `logs:CreateLogGroup` authorization error, which Step
   * Functions then retries into `Glue.ConcurrentRunsExceededException`.
   */
  private grantContinuousLogging(account: string, region: string): void {
    const { partition } = Stack.of(this);
    const logGroupArn = `arn:${partition}:logs:${region}:${account}:log-group:/aws-glue/jobs/*`;
    this.role.addToPolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ],
        resources: [logGroupArn, `${logGroupArn}:log-stream:*`],
      }),
    );
  }

  private suppressNags(): void {
    NagSuppressions.addResourceSuppressions(
      this.role,
      [
        {
          id: "AwsSolutions-IAM5",
          reason:
            "S3 Tables and Glue Data Catalog access is scoped to the one S3 Tables bucket ARN and " +
            "this account's catalog. The object-level '/*' is required because the Iceberg writer " +
            "creates table and metadata object keys at runtime.",
          appliesTo: ["Resource::*"],
        },
      ],
      true,
    );
    NagSuppressions.addResourceSuppressions(this.job, [
      {
        id: "AwsSolutions-GL1",
        reason:
          "CloudWatch Logs encrypts log data at rest by default. A Glue security configuration " +
          "with log encryption needs a customer managed key, which this project does not create.",
      },
      {
        id: "AwsSolutions-GL3",
        reason:
          "Glue stores the job bookmark state itself. A Glue security configuration with " +
          "bookmark encryption needs a customer managed key, which this project does not create.",
      },
    ]);
  }
}
