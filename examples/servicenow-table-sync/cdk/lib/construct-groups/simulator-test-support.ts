import { Duration } from "aws-cdk-lib";
import { LambdaRestApi } from "aws-cdk-lib/aws-apigateway";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import { Architecture, DockerImageCode, DockerImageFunction } from "aws-cdk-lib/aws-lambda";
import { ISecret } from "aws-cdk-lib/aws-secretsmanager";
import {
  AwsCustomResource,
  AwsCustomResourcePolicy,
  PhysicalResourceId,
} from "aws-cdk-lib/custom-resources";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createConstructId, createResourceName } from "../common/config";

/** Configuration for the {@link SimulatorTestSupport} construct group. */
export interface SimulatorTestSupportProps {
  readonly projectName: ProjectName;
  /** Directory holding the simulator Dockerfile (the Lambda image). */
  readonly simulatorImageDirectory: string;
  /** The ServiceNow credentials secret to point at the simulator (seeded here). */
  readonly serviceNowSecret: ISecret;
}

/** The simulator keeps a small deterministic dataset in memory. */
const SIMULATOR_MEMORY_MB = 512;
const SIMULATOR_TIMEOUT = Duration.seconds(30);

/**
 * Test stack support: runs the ServiceNow simulator as a Lambda behind API Gateway
 * and seeds the test credentials secret with the simulator URL.
 *
 * - {@link simulatorApi}: a `LambdaRestApi` fronting the simulator container image
 *   (Mangum over the FastAPI app), so the extraction Lambdas call it over HTTPS
 *   exactly like a real ServiceNow instance.
 * - A custom resource writes the simulator URL and synthetic client credentials to the
 *   test secret.
 *
 * CDK writes the test registry rows to DynamoDB during deployment. This construct
 * owns the simulator and updates the synthetic credentials secret with its URL.
 */
export class SimulatorTestSupport extends Construct {
  public readonly simulatorApi: LambdaRestApi;

  constructor(scope: Construct, id: string, props: SimulatorTestSupportProps) {
    super(scope, id);

    const { projectName, simulatorImageDirectory, serviceNowSecret } = props;

    const simulatorLambda = new DockerImageFunction(
      this,
      createConstructId(ResourceName.SIMULATOR_LAMBDA),
      {
        functionName: createResourceName(projectName, ResourceName.SIMULATOR_LAMBDA),
        code: DockerImageCode.fromImageAsset(simulatorImageDirectory, {
          platform: Platform.LINUX_ARM64,
        }),
        architecture: Architecture.ARM_64,
        memorySize: SIMULATOR_MEMORY_MB,
        timeout: SIMULATOR_TIMEOUT,
      },
    );

    this.simulatorApi = new LambdaRestApi(this, createConstructId(ResourceName.SIMULATOR_API), {
      restApiName: createResourceName(projectName, ResourceName.SIMULATOR_API),
      handler: simulatorLambda,
      deployOptions: { stageName: "sim" },
    });

    this.seedServiceNowSecret(serviceNowSecret);
    this.suppressApiGatewayNags();
  }

  /**
   * Writes the client-credentials secret used by the extractor to point at the simulator.
   * The simulator accepts the synthetic client ID and secret.
   */
  private seedServiceNowSecret(secret: ISecret): void {
    const seeder = new AwsCustomResource(this, createConstructId(ResourceName.SECRET_SEEDER), {
      onUpdate: {
        service: "SecretsManager",
        action: "putSecretValue",
        parameters: {
          SecretId: secret.secretArn,
          SecretString: JSON.stringify({
            instance_url: this.simulatorApi.url.replace(/\/$/, ""),
            client_id: "simulator",
            client_secret: "simulator",
            grant_type: "client_credentials",
          }),
        },
        physicalResourceId: PhysicalResourceId.of(`${secret.secretArn}-simulator`),
      },
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: [secret.secretArn] }),
      installLatestAwsSdk: false,
    });
    secret.grantWrite(seeder);
  }

  private suppressApiGatewayNags(): void {
    NagSuppressions.addResourceSuppressions(
      this.simulatorApi,
      [
        {
          id: "AwsSolutions-APIG2",
          reason: "The simulator is a test-only mock; request validation is not applicable.",
        },
        {
          id: "AwsSolutions-APIG1",
          reason: "Access logging is unnecessary for a throwaway test-only simulator API.",
        },
        {
          id: "AwsSolutions-APIG3",
          reason: "A WAF is unnecessary for the local test simulator API.",
        },
        {
          id: "AwsSolutions-APIG6",
          reason: "CloudWatch method logging is unnecessary for a throwaway test simulator API.",
        },
        {
          id: "AwsSolutions-APIG4",
          reason:
            "The simulator deliberately mimics ServiceNow (its own OAuth token endpoint), so " +
            "API Gateway authorization is intentionally open on this test-only mock.",
        },
        {
          id: "AwsSolutions-COG4",
          reason: "No Cognito authorizer on the test-only simulator mock (see APIG4).",
        },
        {
          id: "AwsSolutions-IAM5",
          reason:
            "The secret-seeding custom resource uses a CDK-generated SDK-call policy scoped to " +
            "the one ServiceNow credentials secret; the wildcard is on that resource's own sub-paths.",
        },
        {
          id: "AwsSolutions-IAM4",
          reason:
            "The secret-seeding custom-resource role uses the CDK default managed policy for logs.",
        },
        {
          id: "AwsSolutions-L1",
          reason:
            "The secret-seeding custom-resource Lambda runtime is CDK-managed, not authored here.",
        },
      ],
      true,
    );
  }
}
