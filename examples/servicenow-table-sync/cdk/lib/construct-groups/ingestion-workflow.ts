import { Duration, RemovalPolicy } from "aws-cdk-lib";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { Schedule, ScheduleExpression } from "aws-cdk-lib/aws-scheduler";
import { StepFunctionsStartExecution } from "aws-cdk-lib/aws-scheduler-targets";
import { Queue, QueueEncryption } from "aws-cdk-lib/aws-sqs";
import {
  Choice,
  Condition,
  DefinitionBody,
  Fail,
  IntegrationPattern,
  JsonPath,
  LogLevel,
  Map as SfnMap,
  Pass,
  StateMachine,
  Succeed,
  TaskInput,
} from "aws-cdk-lib/aws-stepfunctions";
import { GlueStartJobRun, LambdaInvoke } from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import {
  ProjectName,
  ResourceName,
  SYNC_INTERVAL_HOURS,
  createConstructId,
  createResourceName,
} from "../common/config";
import { ExtractionLambdas } from "./extraction-lambdas";
import { NormalizerJob } from "./normalizer-job";

/** ServiceNow allows ~25k requests/hour per integration user; cap parallel table extracts. */
const EXTRACT_MAX_CONCURRENCY = 5;
/** Glue account job-run concurrency is modest; cap parallel normalize runs. */
const NORMALIZE_MAX_CONCURRENCY = 5;

/** The transient-error retrier applied to every Task (step-functions-patterns rule 1). */
const TRANSIENT_RETRY = {
  errors: ["States.ALL"],
  interval: Duration.seconds(2),
  maxAttempts: 3,
  backoffRate: 2.0,
};

/** Configuration for the {@link IngestionWorkflow} construct group. */
export interface IngestionWorkflowProps {
  readonly projectName: ProjectName;
  readonly removalPolicy: RemovalPolicy;
  readonly extractionLambdas: ExtractionLambdas;
  readonly normalizerJob: NormalizerJob;
  /**
   * Whether the recurring EventBridge schedule is enabled. The solution stack passes
   * the configured value, while the test stack always passes false.
   */
  readonly scheduleEnabled: boolean;
}

/**
 * The Step Functions state machine orchestrating one ingestion run, plus its
 * scheduled EventBridge trigger.
 *
 * Flow: `ListTables` -> `ExtractTables` (Map, one `extract_table` Lambda per table
 * in parallel) -> `NormalizeTables` (Map, one Glue job run per table IN PARALLEL,
 * each gated by a `Choice` so the Spark job runs ONLY for a table that extracted
 * new structured records) -> `ExtractDeletes` (Map, one `extract_deletes` Lambda
 * per table over `$.Tables`, results at `$.DeleteResults`) -> `NormalizeDeletedTables`
 * (Map over `$.DeleteResults`, each gated by a `Choice` so the SAME Glue job runs
 * ONLY for a structured table whose deletes pass produced deletions) -> `Succeed`.
 *
 * The upsert normalize runs before the deletes step and the delete normalize, so a
 * record updated and deleted in the same broad window ends deleted: the upsert lands
 * first (no manifest present yet), then `ExtractDeletes` writes the manifest and
 * `NormalizeDeletedTables` applies it (delete wins). Running the delete normalize as
 * a second gate on `$.DeleteResults` (rather than joining the two result arrays)
 * closes the deletion-only bug: a table with deletions but no new records is skipped
 * by `NormalizeTables` yet selected by `NormalizeDeletedTables`.
 *
 * Resilience: every Task carries a `States.ALL` Retry with backoff, and every Map
 * a Catch to a terminal `Fail`, so a transient error retries and an unrecoverable
 * one ends the run visibly. The extract Lambda also checkpoints per table (advancing
 * the high-water mark only on success, recording FAILED otherwise), so a failed run
 * re-extracts from the same point.
 *
 * The log group uses CloudWatch Logs default encryption and the schedule dead-letter
 * queue uses Amazon SQS managed encryption (SSE-SQS).
 */
export class IngestionWorkflow extends Construct {
  public readonly stateMachine: StateMachine;
  public readonly scheduleDlq: Queue;

  constructor(scope: Construct, id: string, props: IngestionWorkflowProps) {
    super(scope, id);

    const { projectName, removalPolicy, extractionLambdas, normalizerJob } = props;

    const failState = new Fail(this, "IngestionFailed", {
      error: "IngestionFailed",
      cause: "A task failed after retries; see the execution history and CloudWatch logs.",
    });

    const definition = this.buildListTables(extractionLambdas, failState)
      .next(this.buildExtractMap(extractionLambdas, failState))
      .next(this.buildNormalizeMap(normalizerJob, failState))
      .next(this.buildExtractDeletesMap(extractionLambdas, failState))
      .next(this.buildNormalizeDeletedMap(normalizerJob, failState))
      .next(new Succeed(this, "IngestionSucceeded"));

    const logGroup = new LogGroup(this, createConstructId("IngestionLogGroup"), {
      retention: RetentionDays.ONE_MONTH,
      removalPolicy,
    });
    this.stateMachine = new StateMachine(
      this,
      createConstructId(ResourceName.INGESTION_STATE_MACHINE),
      {
        stateMachineName: createResourceName(projectName, ResourceName.INGESTION_STATE_MACHINE),
        definitionBody: DefinitionBody.fromChainable(definition),
        tracingEnabled: true,
        logs: { destination: logGroup, level: LogLevel.ALL, includeExecutionData: true },
      },
    );

    this.scheduleDlq = this.buildSchedule(projectName, removalPolicy, props.scheduleEnabled);
  }

  private buildListTables(lambdas: ExtractionLambdas, failState: Fail): LambdaInvoke {
    const task = new LambdaInvoke(this, "ListTables", {
      lambdaFunction: lambdas.listTablesLambda,
      payloadResponseOnly: true,
    });
    task.addRetry(TRANSIENT_RETRY);
    task.addCatch(failState, { resultPath: "$.error" });
    return task;
  }

  private buildExtractMap(lambdas: ExtractionLambdas, failState: Fail): SfnMap {
    const extract = new LambdaInvoke(this, "ExtractTable", {
      lambdaFunction: lambdas.extractTableLambda,
      payloadResponseOnly: true,
    });
    extract.addRetry(TRANSIENT_RETRY);

    const map = new SfnMap(this, "ExtractTables", {
      itemsPath: JsonPath.stringAt("$.Tables"),
      maxConcurrency: EXTRACT_MAX_CONCURRENCY,
      resultPath: "$.ExtractResults",
    });
    map.itemProcessor(extract);
    map.addCatch(failState, { resultPath: "$.error" });
    return map;
  }

  private buildNormalizeMap(normalizerJob: NormalizerJob, failState: Fail): SfnMap {
    // One Glue run per table, gated so the Spark job fires only when the table
    // extracted new structured records (avoids running Spark on no new data).
    const skip = new Pass(this, "SkipNormalize");
    const normalize = this.buildGlueNormalizeTask(normalizerJob, "NormalizeTable");

    const gate = new Choice(this, "HasNewStructuredRecords")
      .when(
        Condition.and(
          Condition.stringEquals("$.Destination", "STRUCTURED"),
          Condition.numberGreaterThan("$.RecordsExtracted", 0),
        ),
        normalize,
      )
      .otherwise(skip);

    const map = new SfnMap(this, "NormalizeTables", {
      itemsPath: JsonPath.stringAt("$.ExtractResults"),
      maxConcurrency: NORMALIZE_MAX_CONCURRENCY,
      resultPath: JsonPath.DISCARD,
    });
    map.itemProcessor(gate);
    map.addCatch(failState, { resultPath: "$.error" });
    return map;
  }

  private buildExtractDeletesMap(lambdas: ExtractionLambdas, failState: Fail): SfnMap {
    // One deletes-Lambda invocation per table, over the same $.Tables array the
    // upsert extract iterated ($.Tables must survive to here, so every prior
    // resultPath merges or discards it, never overwrites it).
    const extract = new LambdaInvoke(this, "ExtractDeletesTask", {
      lambdaFunction: lambdas.extractDeletesLambda,
      payloadResponseOnly: true,
    });
    extract.addRetry(TRANSIENT_RETRY);

    const map = new SfnMap(this, "ExtractDeletes", {
      itemsPath: JsonPath.stringAt("$.Tables"),
      maxConcurrency: EXTRACT_MAX_CONCURRENCY,
      resultPath: "$.DeleteResults",
    });
    map.itemProcessor(extract);
    map.addCatch(failState, { resultPath: "$.error" });
    return map;
  }

  private buildNormalizeDeletedMap(normalizerJob: NormalizerJob, failState: Fail): SfnMap {
    // The same Glue job as the upsert normalize, run a second time after the deletes
    // manifest exists, gated so it fires only for a structured table whose deletes
    // pass produced deletions. This closes the deletion-only bug: a table with
    // RecordsExtracted=0 is skipped by NormalizeTables but selected here whenever
    // DeletesProcessed>0, so its manifest is applied. The gate field is
    // DeletesProcessed (the alias ExtractDeletesResponse emits), not RecordsDeleted.
    const skip = new Pass(this, "SkipDeleteNormalize");
    const normalize = this.buildGlueNormalizeTask(normalizerJob, "NormalizeDeletedTable");

    const gate = new Choice(this, "HasDeletedStructuredRecords")
      .when(
        Condition.and(
          Condition.stringEquals("$.Destination", "STRUCTURED"),
          Condition.numberGreaterThan("$.DeletesProcessed", 0),
        ),
        normalize,
      )
      .otherwise(skip);

    const map = new SfnMap(this, "NormalizeDeletedTables", {
      itemsPath: JsonPath.stringAt("$.DeleteResults"),
      maxConcurrency: NORMALIZE_MAX_CONCURRENCY,
      resultPath: JsonPath.DISCARD,
    });
    map.itemProcessor(gate);
    map.addCatch(failState, { resultPath: "$.error" });
    return map;
  }

  /**
   * Builds a per-table normalize Task invoking the one Glue job via RUN_JOB (.sync)
   * with `--table_name`. Shared by the upsert normalize and the delete-apply
   * normalize: the same job applies the upsert MERGE and `_apply_deletes` on every
   * run, so the two workflow states differ only in their Choice gate and state names,
   * not in the task itself. The transient retry is attached here.
   */
  private buildGlueNormalizeTask(normalizerJob: NormalizerJob, id: string): GlueStartJobRun {
    const normalize = new GlueStartJobRun(this, id, {
      glueJobName: normalizerJob.jobName,
      integrationPattern: IntegrationPattern.RUN_JOB, // .sync, wait for the job to finish
      arguments: TaskInput.fromObject({ "--table_name": JsonPath.stringAt("$.TableName") }),
    });
    normalize.addRetry(TRANSIENT_RETRY);
    return normalize;
  }

  private buildSchedule(
    projectName: ProjectName,
    removalPolicy: RemovalPolicy,
    scheduleEnabled: boolean,
  ): Queue {
    const dlq = new Queue(this, createConstructId(ResourceName.INGESTION_SCHEDULE_DLQ), {
      queueName: createResourceName(projectName, ResourceName.INGESTION_SCHEDULE_DLQ),
      encryption: QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      removalPolicy,
    });

    new Schedule(this, createConstructId(ResourceName.INGESTION_SCHEDULE), {
      scheduleName: createResourceName(projectName, ResourceName.INGESTION_SCHEDULE),
      schedule: ScheduleExpression.rate(Duration.hours(SYNC_INTERVAL_HOURS)),
      // The solution stack passes the explicit schedule setting. The test stack
      // always passes false. Manual StartExecution is unaffected.
      enabled: scheduleEnabled,
      target: new StepFunctionsStartExecution(this.stateMachine, { deadLetterQueue: dlq }),
    });
    return dlq;
  }
}
