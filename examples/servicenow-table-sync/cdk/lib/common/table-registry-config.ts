/** One ServiceNow table to sync. */
export interface TableMetadataConfig {
  /** The ServiceNow table name, for example `incident`. */
  readonly tableName: string;
  /** Set to false to keep the row but skip the table. */
  readonly enabled: boolean;
  /** Comma-separated field names to extract. An empty string extracts every field. */
  readonly fields: string;
  /** An encoded query that filters the rows, for example `active=true`. */
  readonly baseQuery: string;
}

const syncTable = (
  tableName: string,
  overrides: Partial<Omit<TableMetadataConfig, "tableName">> = {},
): TableMetadataConfig => ({
  tableName,
  enabled: overrides.enabled ?? true,
  fields: overrides.fields ?? "",
  baseQuery: overrides.baseQuery ?? "",
});

/**
 * The tables to sync. Edit this list and run `make deploy` again to add or remove a
 * table. The defaults are the standard ServiceNow tables for IT Service Management,
 * the Service Catalog, and the Configuration Management Database (CMDB).
 */
export const TABLE_REGISTRY: readonly TableMetadataConfig[] = [
  syncTable("incident"),
  syncTable("change_request"),
  syncTable("problem"),
  syncTable("sc_request"),
  syncTable("sc_req_item"),
  syncTable("task_sla"),
  syncTable("cmdb_ci_server"),
  syncTable("cmdb_ci_appl"),
];

/** The single table the optional test stack syncs from the simulator. */
export const TEST_TABLE_REGISTRY: readonly TableMetadataConfig[] = TABLE_REGISTRY.filter(
  ({ tableName }) => tableName === "incident",
);

/** Rejects an invalid table list during synthesis. */
export const validateTableRegistry = (rows: readonly TableMetadataConfig[]): void => {
  if (rows.length === 0 || !rows.some(({ enabled }) => enabled)) {
    throw new Error("The table list must contain at least one enabled table.");
  }

  const names = new Set<string>();
  for (const row of rows) {
    if (!/^[a-z][a-z0-9_]*$/.test(row.tableName)) {
      throw new Error(`Invalid ServiceNow table name: ${row.tableName}`);
    }
    if (names.has(row.tableName)) {
      throw new Error(`Duplicate ServiceNow table name: ${row.tableName}`);
    }
    names.add(row.tableName);
  }
};

validateTableRegistry(TABLE_REGISTRY);
validateTableRegistry(TEST_TABLE_REGISTRY);
