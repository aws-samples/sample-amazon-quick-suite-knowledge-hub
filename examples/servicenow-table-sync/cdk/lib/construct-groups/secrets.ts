import { RemovalPolicy, SecretValue } from "aws-cdk-lib";
import { Secret } from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import { ProjectName, ResourceName, createConstructId, createResourceName } from "../common/config";

/** Configuration for the {@link Secrets} construct group. */
export interface SecretsProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
}

/**
 * The test stack's synthetic ServiceNow credentials secret. The seeder rewrites it to
 * point at the simulator. The solution stack does not use this construct: it imports a
 * fixed-name secret that you create before deploying.
 */
export class Secrets extends Construct {
  public readonly serviceNowSecret: Secret;

  constructor(scope: Construct, id: string, props: SecretsProps) {
    super(scope, id);

    const { projectName, removalPolicy } = props;

    this.serviceNowSecret = new Secret(this, createConstructId(ResourceName.SERVICE_NOW_SECRET), {
      secretName: createResourceName(projectName, ResourceName.SERVICE_NOW_SECRET),
      description: "Synthetic ServiceNow credentials for the test stack.",
      removalPolicy,
      secretObjectValue: {
        instance_url: SecretValue.unsafePlainText("https://REPLACE_ME.service-now.com"),
        client_id: SecretValue.unsafePlainText("REPLACE_ME"),
        client_secret: SecretValue.unsafePlainText("REPLACE_ME"),
        grant_type: SecretValue.unsafePlainText("client_credentials"),
      },
    });
  }
}
