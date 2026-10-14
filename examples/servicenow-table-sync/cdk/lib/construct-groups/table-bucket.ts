import { RemovalPolicy } from "aws-cdk-lib";
import { CfnNamespace, CfnTableBucket } from "aws-cdk-lib/aws-s3tables";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createBucketName, createConstructId } from "../common/config";

/** Days S3 Tables waits before it removes noncurrent and unreferenced table files. */
const FILE_REMOVAL_DELAY_DAYS = 3;

/** Configuration for the {@link TableBucket} construct group. */
export interface TableBucketProps {
  readonly projectName: ProjectName;
  readonly account: string;
  readonly region: string;
  /** The Iceberg namespace to create in the table bucket. */
  readonly namespace: string;
  readonly removalPolicy: RemovalPolicy;
}

/**
 * The S3 Tables (Apache Iceberg) bucket and namespace the Glue job merges into. The
 * bucket uses Amazon S3 managed encryption (SSE-S3). S3 Tables accepts only customer
 * managed keys for SSE-KMS, so SSE-S3 is the service-managed option.
 */
export class TableBucket extends Construct {
  public readonly bucketArn: string;
  public readonly namespaceName: string;

  constructor(scope: Construct, id: string, props: TableBucketProps) {
    super(scope, id);

    const { projectName, account, region, namespace, removalPolicy } = props;

    const bucket = new CfnTableBucket(this, createConstructId(ResourceName.S3_TABLES_BUCKET), {
      tableBucketName: createBucketName(
        projectName,
        ResourceName.S3_TABLES_BUCKET,
        account,
        region,
      ),
      encryptionConfiguration: { sseAlgorithm: "AES256" },
      unreferencedFileRemoval: {
        status: "Enabled",
        noncurrentDays: FILE_REMOVAL_DELAY_DAYS,
        unreferencedDays: FILE_REMOVAL_DELAY_DAYS,
      },
    });
    bucket.applyRemovalPolicy(removalPolicy);

    const ns = new CfnNamespace(this, createConstructId("S3TablesNamespace"), {
      tableBucketArn: bucket.attrTableBucketArn,
      namespace,
    });
    ns.addResourceDependency(bucket);

    this.bucketArn = bucket.attrTableBucketArn;
    this.namespaceName = namespace;
  }
}
