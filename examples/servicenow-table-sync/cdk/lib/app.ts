#!/usr/bin/env node
import { Annotations, App, Aspects, Stack, Tags } from "aws-cdk-lib";
import { AwsSolutionsChecks, NagSuppressions } from "cdk-nag";
import * as path from "path";
import {
  ProjectName,
  createConstructId,
  createStackName,
  resolveScheduleEnabled,
  resolveTestStackEnabled,
} from "./common/config";
import { NAG_SUPPRESSIONS } from "./common/nag-suppressions";
import { ServiceNowIngestionStack } from "./stacks/service-now-ingestion-stack";
import { ServiceNowIngestionTestStack } from "./stacks/service-now-ingestion-test-stack";

const DEFAULT_REGION = "us-east-1";

const app = new App();

const account = process.env.CDK_DEFAULT_ACCOUNT ?? "";
const region = process.env.CDK_DEFAULT_REGION ?? DEFAULT_REGION;
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };

const getContext = (key: string): string | undefined =>
  app.node.tryGetContext(key) as string | undefined;

const solutionStack = new ServiceNowIngestionStack(
  app,
  createConstructId(createStackName(ProjectName.SERVICE_NOW_INGESTION)),
  {
    projectName: ProjectName.SERVICE_NOW_INGESTION,
    account,
    region,
    scheduleEnabled: resolveScheduleEnabled(getContext("enableSchedule")),
    env,
    description: "ServiceNow table sync for Amazon Quick",
  },
);

const stacks: Stack[] = [solutionStack];
Tags.of(solutionStack).add("project", ProjectName.SERVICE_NOW_INGESTION);

const costCenter = getContext("costCenter");
if (costCenter !== undefined) {
  Tags.of(solutionStack).add("cost-center", costCenter);
}

if (resolveTestStackEnabled(getContext("enableTestStack"))) {
  const testStack = new ServiceNowIngestionTestStack(
    app,
    createConstructId(createStackName(ProjectName.SERVICE_NOW_INGESTION, "Test")),
    {
      projectName: ProjectName.SERVICE_NOW_INGESTION,
      account,
      region,
      simulatorImageDirectory: path.join(__dirname, "..", "..", "simulator"),
      env,
      description: "ServiceNow table sync test stack with a simulator",
    },
  );
  Tags.of(testStack).add("project", ProjectName.SERVICE_NOW_INGESTION);
  stacks.push(testStack);
}

Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
for (const stack of stacks) {
  NagSuppressions.addStackSuppressions(stack, [...NAG_SUPPRESSIONS]);
}

if (process.env.CDK_DEFAULT_ACCOUNT === undefined) {
  for (const stack of stacks) {
    Annotations.of(stack).addInfo(
      "No CDK_DEFAULT_ACCOUNT set. Synthesizing an environment-agnostic template.",
    );
  }
}

app.synth();
