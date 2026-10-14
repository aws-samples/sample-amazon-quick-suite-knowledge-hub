import { Duration, RemovalPolicy } from "aws-cdk-lib";
import { Grant, IGrantable } from "aws-cdk-lib/aws-iam";
import {
  BlockPublicAccess,
  Bucket,
  BucketEncryption,
  ObjectOwnership,
  StorageClass,
} from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createBucketName, createConstructId } from "../common/config";

/** Days a staged object stays in S3 Standard before it moves to Infrequent Access. */
const DAYS_BEFORE_INFREQUENT_ACCESS = 30;

/** Days a staged object stays before it moves to Glacier Instant Retrieval. */
const DAYS_BEFORE_GLACIER_INSTANT_RETRIEVAL = 90;

/** Configuration for the {@link DataLake} construct group. */
export interface DataLakeProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
  readonly account: string;
  readonly region: string;
}

/**
 * The staging bucket and its access-log bucket. The staging bucket holds Parquet under
 * `staging/{table}/`. Both buckets block public access, require TLS, use Amazon S3
 * managed encryption (SSE-S3), enable versioning, and apply the supplied removal policy.
 * A lifecycle rule moves older staging objects to lower-cost storage.
 */
export class DataLake extends Construct {
  public readonly rawDataLakeBucket: Bucket;
  public readonly accessLogsBucket: Bucket;

  constructor(scope: Construct, id: string, props: DataLakeProps) {
    super(scope, id);

    const { projectName, removalPolicy, account, region } = props;

    this.accessLogsBucket = new Bucket(this, createConstructId(ResourceName.ACCESS_LOGS_BUCKET), {
      bucketName: createBucketName(projectName, ResourceName.ACCESS_LOGS_BUCKET, account, region),
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      objectOwnership: ObjectOwnership.BUCKET_OWNER_PREFERRED,
      removalPolicy,
      autoDeleteObjects: true,
    });

    this.rawDataLakeBucket = new Bucket(
      this,
      createConstructId(ResourceName.RAW_DATA_LAKE_BUCKET),
      {
        bucketName: createBucketName(
          projectName,
          ResourceName.RAW_DATA_LAKE_BUCKET,
          account,
          region,
        ),
        encryption: BucketEncryption.S3_MANAGED,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        versioned: true,
        serverAccessLogsBucket: this.accessLogsBucket,
        serverAccessLogsPrefix: "raw-data-lake/",
        removalPolicy,
        autoDeleteObjects: true,
        lifecycleRules: [
          {
            id: "TierAgingRawObjects",
            transitions: [
              {
                storageClass: StorageClass.INFREQUENT_ACCESS,
                transitionAfter: Duration.days(DAYS_BEFORE_INFREQUENT_ACCESS),
              },
              {
                storageClass: StorageClass.GLACIER_INSTANT_RETRIEVAL,
                transitionAfter: Duration.days(DAYS_BEFORE_GLACIER_INSTANT_RETRIEVAL),
              },
            ],
          },
        ],
      },
    );
  }

  /** Grants a principal read/write on the staging bucket. */
  public grantReadWrite(grantee: IGrantable): Grant {
    return this.rawDataLakeBucket.grantReadWrite(grantee);
  }

  /** Grants a principal read on the staging bucket. */
  public grantRead(grantee: IGrantable): Grant {
    return this.rawDataLakeBucket.grantRead(grantee);
  }
}
