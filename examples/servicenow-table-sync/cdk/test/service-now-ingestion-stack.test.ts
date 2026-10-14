import { App, Aspects, Stack } from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks, NagSuppressions } from "cdk-nag";
import * as path from "path";
import {
  ProjectName,
  SYNC_INTERVAL_HOURS,
  createConstructId,
  createStackName,
  resolveScheduleEnabled,
  resolveTestStackEnabled,
} from "../lib/common/config";
import { NAG_SUPPRESSIONS } from "../lib/common/nag-suppressions";
import {
  TABLE_REGISTRY,
  TEST_TABLE_REGISTRY,
  validateTableRegistry,
} from "../lib/common/table-registry-config";
import { ServiceNowIngestionStack } from "../lib/stacks/service-now-ingestion-stack";
import { ServiceNowIngestionTestStack } from "../lib/stacks/service-now-ingestion-test-stack";

const ACCOUNT = "111122223333";
const REGION = "us-east-1";
const SECRET_NAME = "ServiceNowIngestionServiceNowCredentials";
const SIMULATOR_DIRECTORY = path.join(__dirname, "..", "..", "simulator");

const DEFAULT_TABLES = [
  "incident",
  "change_request",
  "problem",
  "sc_request",
  "sc_req_item",
  "task_sla",
  "cmdb_ci_server",
  "cmdb_ci_appl",
];

interface SolutionOptions {
  readonly scheduleEnabled?: boolean;
  readonly applyNag?: boolean;
}

function buildSolutionStack(options: SolutionOptions = {}): ServiceNowIngestionStack {
  const app = new App();
  const stack = new ServiceNowIngestionStack(
    app,
    createConstructId(createStackName(ProjectName.SERVICE_NOW_INGESTION)),
    {
      projectName: ProjectName.SERVICE_NOW_INGESTION,
      account: ACCOUNT,
      region: REGION,
      scheduleEnabled: options.scheduleEnabled ?? true,
      env: { account: ACCOUNT, region: REGION },
    },
  );
  if (options.applyNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
    NagSuppressions.addStackSuppressions(stack, [...NAG_SUPPRESSIONS]);
  }
  return stack;
}

function buildTestStack(applyNag = false): ServiceNowIngestionTestStack {
  const app = new App();
  const stack = new ServiceNowIngestionTestStack(
    app,
    createConstructId(createStackName(ProjectName.SERVICE_NOW_INGESTION, "Test")),
    {
      projectName: ProjectName.SERVICE_NOW_INGESTION,
      account: ACCOUNT,
      region: REGION,
      simulatorImageDirectory: SIMULATOR_DIRECTORY,
      env: { account: ACCOUNT, region: REGION },
    },
  );
  if (applyNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
    NagSuppressions.addStackSuppressions(stack, [...NAG_SUPPRESSIONS]);
  }
  return stack;
}

const solutionTemplate = (options: SolutionOptions = {}): Template =>
  Template.fromStack(buildSolutionStack(options));

const testTemplate = (): Template => Template.fromStack(buildTestStack());

interface PolicyStatement {
  Action: string | string[];
  Resource: unknown;
}

function allPolicyStatements(template: Template): PolicyStatement[] {
  return Object.values(template.findResources("AWS::IAM::Policy")).flatMap(
    (policy) =>
      (policy.Properties as { PolicyDocument: { Statement: PolicyStatement[] } }).PolicyDocument
        .Statement,
  );
}

const actionsOf = (statement: PolicyStatement): string[] =>
  Array.isArray(statement.Action) ? statement.Action : [statement.Action];

function expectResourceType(template: Template, resourceType: string): void {
  expect(Object.keys(template.findResources(resourceType)).length).toBeGreaterThan(0);
}

const PIPELINE_RESOURCE_TYPES = [
  "AWS::DynamoDB::Table",
  "AWS::S3::Bucket",
  "AWS::S3Tables::TableBucket",
  "AWS::S3Tables::Namespace",
  "AWS::Glue::Job",
  "AWS::StepFunctions::StateMachine",
  "AWS::Scheduler::Schedule",
  "AWS::Lambda::Function",
];

function expectNoNagErrors(stack: Stack): void {
  const errors = Annotations.fromStack(stack).findError(
    "*",
    Match.stringLikeRegexp("AwsSolutions-.*"),
  );
  expect(errors.map((error) => JSON.stringify(error.entry.data))).toEqual([]);
}

describe("context flags", () => {
  it("leaves the test stack out unless the operator opts in", () => {
    expect(resolveTestStackEnabled(undefined)).toBe(false);
    expect(resolveTestStackEnabled("false")).toBe(false);
    expect(resolveTestStackEnabled("true")).toBe(true);
  });

  it("runs the schedule unless the operator opts out", () => {
    expect(resolveScheduleEnabled(undefined)).toBe(true);
    expect(resolveScheduleEnabled("true")).toBe(true);
    expect(resolveScheduleEnabled("false")).toBe(false);
  });

  it("rejects a flag value that is not true or false", () => {
    expect(() => resolveScheduleEnabled("yes")).toThrow(/Unsupported boolean value "yes"/);
    expect(() => resolveTestStackEnabled("1")).toThrow(/Unsupported boolean value "1"/);
  });
});

describe("solution stack resources", () => {
  it("creates the sync pipeline resources", () => {
    const template = solutionTemplate();
    for (const resourceType of PIPELINE_RESOURCE_TYPES) {
      expectResourceType(template, resourceType);
    }
  });

  it("builds every extraction function from a container image", () => {
    const functions = Object.values(solutionTemplate().findResources("AWS::Lambda::Function"));
    const imageFunctions = functions.filter((fn) => fn.Properties.PackageType === "Image");
    expect(imageFunctions.length).toBeGreaterThan(0);
  });

  it("creates no knowledge base, alarm, dashboard, or API", () => {
    const template = solutionTemplate();
    template.resourceCountIs("AWS::Bedrock::KnowledgeBase", 0);
    template.resourceCountIs("AWS::Bedrock::DataSource", 0);
    template.resourceCountIs("AWS::CloudWatch::Alarm", 0);
    template.resourceCountIs("AWS::CloudWatch::Dashboard", 0);
    template.resourceCountIs("AWS::ApiGateway::RestApi", 0);
  });

  it("imports the ServiceNow secret by name instead of creating it", () => {
    const template = solutionTemplate();
    template.resourceCountIs("AWS::SecretsManager::Secret", 0);
    expect(JSON.stringify(template.toJSON())).toContain(SECRET_NAME);
  });

  it("publishes the table bucket ARN and the state machine ARN", () => {
    const outputs = (solutionTemplate().toJSON().Outputs ?? {}) as Record<string, unknown>;
    expect(Object.keys(outputs).sort()).toEqual(["StateMachineArn", "TableBucketArn"]);
  });

  it("has no cross-stack import", () => {
    expect(JSON.stringify(solutionTemplate().toJSON())).not.toContain("Fn::ImportValue");
  });
});

describe("service-managed encryption", () => {
  it("creates no KMS key", () => {
    solutionTemplate().resourceCountIs("AWS::KMS::Key", 0);
    testTemplate().resourceCountIs("AWS::KMS::Key", 0);
  });

  it("encrypts the table bucket with SSE-S3", () => {
    for (const template of [solutionTemplate(), testTemplate()]) {
      template.hasResourceProperties("AWS::S3Tables::TableBucket", {
        EncryptionConfiguration: { SSEAlgorithm: "AES256" },
      });
    }
  });

  it("encrypts every bucket with SSE-S3 and blocks public access", () => {
    const buckets = Object.values(solutionTemplate().findResources("AWS::S3::Bucket"));
    expect(buckets.length).toBeGreaterThan(0);
    for (const bucket of buckets) {
      expect(bucket.Properties.BucketEncryption.ServerSideEncryptionConfiguration).toEqual([
        { ServerSideEncryptionByDefault: { SSEAlgorithm: "AES256" } },
      ]);
      expect(bucket.Properties.PublicAccessBlockConfiguration).toEqual({
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      });
    }
  });

  it("encrypts every DynamoDB table with the AWS managed key", () => {
    const tables = Object.values(solutionTemplate().findResources("AWS::DynamoDB::Table"));
    expect(tables.length).toBeGreaterThan(0);
    for (const table of tables) {
      expect(table.Properties.SSESpecification).toEqual({ SSEEnabled: true });
    }
  });

  it("encrypts the schedule dead-letter queue with SSE-SQS", () => {
    solutionTemplate().hasResourceProperties("AWS::SQS::Queue", { SqsManagedSseEnabled: true });
  });
});

describe("schedule", () => {
  it("runs at the configured interval", () => {
    solutionTemplate().hasResourceProperties("AWS::Scheduler::Schedule", {
      ScheduleExpression: Match.stringLikeRegexp(`^rate\\(${SYNC_INTERVAL_HOURS} hours?\\)$`),
    });
  });

  it("is enabled by default", () => {
    solutionTemplate().hasResourceProperties("AWS::Scheduler::Schedule", { State: "ENABLED" });
  });

  it("is disabled when the operator opts out", () => {
    solutionTemplate({ scheduleEnabled: false }).hasResourceProperties("AWS::Scheduler::Schedule", {
      State: "DISABLED",
    });
  });

  it("is always disabled in the test stack", () => {
    testTemplate().hasResourceProperties("AWS::Scheduler::Schedule", { State: "DISABLED" });
  });
});

describe("table list", () => {
  it("defaults to the standard ServiceNow tables", () => {
    expect(TABLE_REGISTRY.map(({ tableName }) => tableName)).toEqual(DEFAULT_TABLES);
  });

  it("writes one row to DynamoDB per configured table", () => {
    const rows = JSON.stringify(solutionTemplate().findResources("Custom::AWS"));
    for (const tableName of DEFAULT_TABLES) {
      expect(rows).toContain(tableName);
    }
    expect(Object.keys(solutionTemplate().findResources("Custom::AWS"))).toHaveLength(
      DEFAULT_TABLES.length,
    );
  });

  it("removes a row from DynamoDB when its table leaves the list", () => {
    for (const resource of Object.values(solutionTemplate().findResources("Custom::AWS"))) {
      expect(JSON.stringify(resource.Properties)).toContain("deleteItem");
    }
  });

  it("limits the test stack to the incident table", () => {
    expect(TEST_TABLE_REGISTRY.map(({ tableName }) => tableName)).toEqual(["incident"]);
  });

  it("rejects an empty list, a duplicate, and an invalid name", () => {
    expect(() => validateTableRegistry([])).toThrow(/at least one enabled/);
    expect(() => validateTableRegistry([TABLE_REGISTRY[0], TABLE_REGISTRY[0]])).toThrow(
      /Duplicate/,
    );
    expect(() =>
      validateTableRegistry([
        { tableName: "Invalid-Name", enabled: true, fields: "", baseQuery: "" },
      ]),
    ).toThrow(/Invalid/);
  });
});

describe("workflow", () => {
  interface AslState {
    Type?: string;
    Next?: string;
    ItemsPath?: string;
    ItemProcessor?: { StartAt: string; States: Record<string, AslState> };
    Choices?: Array<{
      And?: Array<{ Variable: string; StringEquals?: string; NumericGreaterThan?: number }>;
      Next: string;
    }>;
    Parameters?: { JobName?: string; Arguments?: Record<string, string> };
  }
  interface Asl {
    States: Record<string, AslState>;
  }

  function stateMachineAsl(template: Template): Asl {
    const [machine] = Object.values(template.findResources("AWS::StepFunctions::StateMachine"));
    const definition = (machine.Properties as { DefinitionString: unknown }).DefinitionString;
    const [, parts] = (definition as { "Fn::Join": [string, unknown[]] })["Fn::Join"];
    const joined = parts.map((part) => (typeof part === "string" ? part : "<TOKEN>")).join("");
    return JSON.parse(joined) as Asl;
  }

  it("applies deletes after the upsert merge, then succeeds", () => {
    const { States } = stateMachineAsl(solutionTemplate());
    expect(States.NormalizeTables.Next).toBe("ExtractDeletes");
    expect(States.ExtractDeletes.Next).toBe("NormalizeDeletedTables");
    expect(States.NormalizeDeletedTables.Next).toBe("IngestionSucceeded");
    expect(States.IngestionSucceeded.Type).toBe("Succeed");
  });

  it("starts the merge job only for a table with new records", () => {
    const { States } = stateMachineAsl(solutionTemplate());
    const map = States.NormalizeTables;
    const inner = map.ItemProcessor!.States;
    const gate = inner[map.ItemProcessor!.StartAt];
    expect(gate.Choices![0].And).toEqual(
      expect.arrayContaining([
        { Variable: "$.Destination", StringEquals: "STRUCTURED" },
        { Variable: "$.RecordsExtracted", NumericGreaterThan: 0 },
      ]),
    );
    const task = inner[gate.Choices![0].Next];
    expect(task.Parameters?.JobName).toBe("ServiceNowIngestionContentNormalizerJob");
    expect(task.Parameters?.Arguments).toEqual({ "--table_name.$": "$.TableName" });
  });

  it("starts the merge job for deletions only when a table has deletes", () => {
    const { States } = stateMachineAsl(solutionTemplate());
    const map = States.NormalizeDeletedTables;
    expect(map.ItemsPath).toBe("$.DeleteResults");
    const gate = map.ItemProcessor!.States[map.ItemProcessor!.StartAt];
    const variables = (gate.Choices![0].And ?? []).map((condition) => condition.Variable);
    expect(variables).toContain("$.DeletesProcessed");
    expect(variables).not.toContain("$.RecordsExtracted");
  });

  it("runs each Lambda handler from an ARM64 image", () => {
    const commands = Object.values(solutionTemplate().findResources("AWS::Lambda::Function"))
      .filter((fn) => fn.Properties.PackageType === "Image")
      .map((fn) => {
        expect(fn.Properties.Architectures).toEqual(["arm64"]);
        return (fn.Properties.ImageConfig.Command as string[])[0];
      })
      .sort();
    expect(commands).toEqual([
      "extract_deletes_handler.handler",
      "extract_table_handler.handler",
      "list_tables_handler.handler",
    ]);
  });
});

describe("least privilege", () => {
  it("scopes every secret read to a specific ARN", () => {
    const reads = allPolicyStatements(solutionTemplate()).filter((statement) =>
      actionsOf(statement).some((action) => action.startsWith("secretsmanager:")),
    );
    expect(reads.length).toBeGreaterThan(0);
    for (const statement of reads) {
      expect(statement.Resource).not.toBe("*");
      expect(actionsOf(statement)).toEqual(
        expect.arrayContaining(["secretsmanager:GetSecretValue"]),
      );
    }
  });

  it("grants the Glue job only the CloudWatch Logs actions continuous logging needs", () => {
    const statements = allPolicyStatements(solutionTemplate()).filter((statement) =>
      actionsOf(statement).includes("logs:CreateLogGroup"),
    );
    expect(statements.length).toBeGreaterThan(0);
    for (const statement of statements) {
      expect(actionsOf(statement).sort()).toEqual([
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:DescribeLogStreams",
        "logs:PutLogEvents",
      ]);
      for (const resource of statement.Resource as unknown[]) {
        expect(JSON.stringify(resource)).toContain(
          `:logs:${REGION}:${ACCOUNT}:log-group:/aws-glue/jobs/*`,
        );
      }
    }
  });

  it("grants no KMS permission to any role", () => {
    for (const template of [solutionTemplate(), testTemplate()]) {
      const kmsStatements = allPolicyStatements(template).filter((statement) =>
        actionsOf(statement).some((action) => action.startsWith("kms:")),
      );
      expect(kmsStatements).toEqual([]);
    }
  });

  it("passes cdk-nag on the solution stack", () => {
    const stack = buildSolutionStack({ applyNag: true });
    Template.fromStack(stack);
    expectNoNagErrors(stack);
  });

  it("passes cdk-nag on the test stack", () => {
    const stack = buildTestStack(true);
    Template.fromStack(stack);
    expectNoNagErrors(stack);
  });
});

describe("test stack", () => {
  it("creates its own secret, table bucket, simulator, and secret seeder", () => {
    const template = testTemplate();
    expectResourceType(template, "AWS::SecretsManager::Secret");
    expectResourceType(template, "AWS::S3Tables::TableBucket");
    expectResourceType(template, "AWS::ApiGateway::RestApi");
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "ServiceNowIngestionSimulatorLambda",
      PackageType: "Image",
    });
  });

  it("adds no simulator to the solution stack", () => {
    const json = JSON.stringify(solutionTemplate().toJSON());
    expect(json).not.toContain("SimulatorLambda");
    expect(json).not.toContain("putSecretValue");
  });
});
