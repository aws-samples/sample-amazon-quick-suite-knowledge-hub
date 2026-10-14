import { NagPackSuppression } from "cdk-nag";

/**
 * Stack-level cdk-nag suppressions. Each carries a reason and, where the rule is
 * granular, an `appliesTo` that scopes it to the finding.
 */
export const NAG_SUPPRESSIONS: ReadonlyArray<NagPackSuppression> = [
  {
    id: "AwsSolutions-IAM4",
    reason:
      "The Lambda execution roles use the AWS managed AWSLambdaBasicExecutionRole for " +
      "CloudWatch Logs write, which is the CDK default for Lambda.",
    appliesTo: [
      "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    ],
  },
  {
    id: "AwsSolutions-IAM5",
    reason:
      "CDK grant methods and the Step Functions Lambda-invoke and Glue-run tasks emit these " +
      "wildcards. Each is scoped to a specific resource: the staging bucket, the named " +
      "functions, the S3 Tables bucket, or this account's Glue catalog. The trailing '*' is " +
      "part of the action name (for example s3:GetObject*) or the object or version suffix.",
    appliesTo: [
      "Action::s3:GetObject*",
      "Action::s3:GetBucket*",
      "Action::s3:List*",
      "Action::s3:DeleteObject*",
      "Action::s3:Abort*",
      { regex: "/^Resource::.*$/" },
    ],
  },
  {
    id: "AwsSolutions-SQS3",
    reason:
      "This queue is the dead-letter queue for the scheduler target. It needs no further queue.",
  },
  {
    id: "AwsSolutions-SMG4",
    reason:
      "The test stack's secret holds synthetic values for the simulator. A real ServiceNow " +
      "OAuth application owns and rotates the real credentials, not AWS Secrets Manager.",
  },
];
