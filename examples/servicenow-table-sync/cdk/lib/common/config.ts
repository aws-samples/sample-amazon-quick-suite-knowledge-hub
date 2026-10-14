/** Project prefix from which all concrete AWS resource names derive. */
export enum ProjectName {
  SERVICE_NOW_INGESTION = "ServiceNowIngestion",
}

/** The Iceberg namespace each stack creates in its own S3 Tables bucket. */
export const S3_TABLES_NAMESPACE = "servicenow";

/** Hours between scheduled syncs. Change it and run `make deploy` to sync more or less often. */
export const SYNC_INTERVAL_HOURS = 6;

/** Logical resource names combined with {@link ProjectName} to derive concrete AWS names. */
export enum ResourceName {
  INGESTION_CHECKPOINT_TABLE = "IngestionCheckpointTable",
  TABLE_METADATA_TABLE = "TableMetadataTable",

  RAW_DATA_LAKE_BUCKET = "raw-data-lake",
  ACCESS_LOGS_BUCKET = "access-logs",
  S3_TABLES_BUCKET = "normalized",

  SERVICE_NOW_SECRET = "ServiceNowCredentials",

  LIST_TABLES_LAMBDA = "ListTablesLambda",
  EXTRACT_TABLE_LAMBDA = "ExtractTableLambda",
  EXTRACT_DELETES_LAMBDA = "ExtractDeletesLambda",

  NORMALIZER_JOB = "ContentNormalizerJob",
  NORMALIZER_ROLE = "ContentNormalizerRole",

  INGESTION_STATE_MACHINE = "IngestionStateMachine",
  INGESTION_SCHEDULE = "IngestionSchedule",
  INGESTION_SCHEDULE_DLQ = "IngestionScheduleDlq",

  SIMULATOR_LAMBDA = "SimulatorLambda",
  SIMULATOR_API = "SimulatorApi",
  SECRET_SEEDER = "ServiceNowSecretSeeder",
}

/** Combines a project name with a PascalCase resource name into a static resource name. */
export const createResourceName = (projectName: ProjectName, resourceName: string): string =>
  `${projectName}${resourceName}`;

/**
 * Builds a globally unique S3 bucket name from the project, a purpose, the account, and
 * the Region. S3 forbids uppercase, so the result is lowercased.
 */
export const createBucketName = (
  projectName: ProjectName,
  bucketName: string,
  account: string,
  region: string,
): string => {
  const kebabProjectName = projectName
    .toString()
    .replace(/([A-Z])/g, "-$1")
    .replace(/^-/, "")
    .toLowerCase();
  return `${kebabProjectName}-${bucketName}-${account}-${region}`.toLowerCase();
};

/** Builds a fixed stack name from the project name and an optional role qualifier. */
export const createStackName = (projectName: ProjectName, qualifier?: string): string => {
  if (qualifier === undefined) {
    return `${projectName}Stack`;
  }
  const pascalQualifier = qualifier.charAt(0).toUpperCase() + qualifier.slice(1);
  return `${projectName}${pascalQualifier}Stack`;
};

/** Capitalizes a resource name for use as a CDK construct id. */
export const createConstructId = (resourceName: string): string =>
  resourceName.charAt(0).toUpperCase() + resourceName.slice(1);

/** Parses a strict boolean context value: only `true` or `false` (any case) is accepted. */
const parseStrictBoolean = (
  contextKey: string,
  value: string | undefined,
  fallback: boolean,
): boolean => {
  if (value === undefined) {
    return fallback;
  }
  const normalized = value.trim().toLowerCase();
  if (normalized === "true") {
    return true;
  }
  if (normalized === "false") {
    return false;
  }
  throw new Error(
    `Unsupported boolean value "${value}" for context "${contextKey}". Use "true" or "false".`,
  );
};

/** Context key that turns the recurring schedule off (solution stack only). */
export const SCHEDULE_CONTEXT_KEY = "enableSchedule";

/** The schedule runs by default. Pass `--context enableSchedule=false` to turn it off. */
export const resolveScheduleEnabled = (value: string | undefined): boolean =>
  parseStrictBoolean(SCHEDULE_CONTEXT_KEY, value, true);

/** Context key that adds the simulator-backed test stack to the app. */
export const TEST_STACK_CONTEXT_KEY = "enableTestStack";

/** The test stack is off by default. Pass `--context enableTestStack=true` to include it. */
export const resolveTestStackEnabled = (value: string | undefined): boolean =>
  parseStrictBoolean(TEST_STACK_CONTEXT_KEY, value, false);
