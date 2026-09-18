import { RemovalPolicy } from 'aws-cdk-lib';
import {
  Bucket,
  BucketEncryption,
  BlockPublicAccess,
} from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import {
  createConstructId,
  createBucketName,
  ProjectName,
} from '../common/config';

export interface StorageBucketsProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
  readonly retainResources: boolean;
  readonly account: string;
  readonly region: string;
}

/** S3 bucket for storing exported SharePoint list CSV files. */
export class StorageBuckets extends Construct {
  public readonly exportBucket: Bucket;

  constructor(scope: Construct, id: string, props: StorageBucketsProps) {
    super(scope, id);

    const { projectName, removalPolicy, retainResources, account, region } =
      props;

    this.exportBucket = new Bucket(this, createConstructId('ExportBucket'), {
      bucketName: createBucketName(projectName, 'exports', account, region),
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy,
      autoDeleteObjects: !retainResources,
    });
  }
}
