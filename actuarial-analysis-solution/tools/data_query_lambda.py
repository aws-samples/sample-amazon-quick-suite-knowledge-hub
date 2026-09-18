"""
Data Query Lambda Handler
========================
Lambda function for data discovery and SQL query execution against AWS Glue Data Catalog and Athena.

Key Features:
- Lists available databases and tables from AWS Glue Data Catalog
- Provides table schema information (columns, data types)
- Executes SQL queries against data sources using AWS Athena
- Returns complete query results with session management
- Integrates with S3 for data storage and parquet file analysis

Supported Operations:
- list_tables: Discover available databases and tables
- describe_table: Get table schema and column information
- run_query: Execute SQL queries and return results with session ID

Used for data exploration and preparation before actuarial analysis.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any

import awswrangler as wr
import boto3
from utils.data_utils import store_session_metadata

# Set up logging
# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize AWS clients
athena = boto3.client("athena")
s3 = boto3.client("s3")

# Environment variables
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID")
ACTOR_ID = os.environ.get("ACTOR_ID", "ActuarialAgent")


def list_tables() -> dict[str, Any]:
    """Lists all tables in the Athena database"""
    try:
        database_name = os.environ.get("ATHENA_DATABASE", "claims_db")

        # Use Glue to list tables
        glue = boto3.client("glue")

        # First check if database exists
        try:
            glue.get_database(Name=database_name)
        except Exception as db_error:
            return {
                "success": False,
                "error": f'Database "{database_name}" not found. Error: {str(db_error)}. The Glue database may not be created yet.',
            }

        response = glue.get_tables(DatabaseName=database_name)

        tables = []
        for table in response.get("TableList", []):
            tables.append(
                {
                    "name": table["Name"],
                    "columns": [
                        col["Name"]
                        for col in table.get("StorageDescriptor", {}).get("Columns", [])
                    ],
                }
            )

        if not tables:
            return {
                "success": True,
                "tables": [],
                "database": database_name,
                "message": f'No tables found in database "{database_name}". The Glue crawler may not have run yet. Please run the crawler to discover tables from S3 data.',
            }

        return {"success": True, "tables": tables, "database": database_name}

    except Exception as e:
        return {"success": False, "error": f"Failed to list tables: {str(e)}"}


def describe_table() -> dict[str, Any]:
    """Describes the structure of the default table"""
    try:
        database_name = os.environ.get("ATHENA_DATABASE", "claims_db")
        table_name = os.environ.get("DEFAULT_TABLE_NAME", "claims")

        # Use Glue to get table details
        glue = boto3.client("glue")

        # First check if database exists and list available tables
        try:
            glue.get_database(Name=database_name)
            tables_response = glue.get_tables(DatabaseName=database_name)
            available_tables = [t["Name"] for t in tables_response.get("TableList", [])]
        except Exception as db_error:
            return {
                "success": False,
                "error": f'Database "{database_name}" not found. Error: {str(db_error)}',
            }

        if not available_tables:
            return {
                "success": False,
                "error": f'No tables found in database "{database_name}". The Glue crawler may not have run yet. Please run the crawler first or check if data exists in S3.',
            }

        if table_name not in available_tables:
            return {
                "success": False,
                "error": f'Table "{table_name}" not found in database "{database_name}". Available tables: {", ".join(available_tables)}',
            }

        response = glue.get_table(DatabaseName=database_name, Name=table_name)

        table = response["Table"]
        columns = []

        for col in table.get("StorageDescriptor", {}).get("Columns", []):
            columns.append(
                {
                    "name": col["Name"],
                    "type": col["Type"],
                    "comment": col.get("Comment", ""),
                }
            )

        return {
            "success": True,
            "table_name": table_name,
            "database_name": database_name,
            "columns": columns,
            "location": table.get("StorageDescriptor", {}).get("Location", ""),
            "input_format": table.get("StorageDescriptor", {}).get("InputFormat", ""),
            "output_format": table.get("StorageDescriptor", {}).get("OutputFormat", ""),
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to describe table: {str(e)}"}


def wait_for_athena_query(athena, query_execution_id, delay=1, max_attempts=300):
    attempts = 0
    while attempts < max_attempts:
        try:
            result = athena.get_query_execution(QueryExecutionId=query_execution_id)
            status = result["QueryExecution"]["Status"]["State"]
        except Exception as e:
            raise RuntimeError(f"Failed to get query status: {str(e)}") from e

        if status == "SUCCEEDED":
            return result
        elif status in ("FAILED", "CANCELLED"):
            error_reason = result["QueryExecution"]["Status"].get(
                "StateChangeReason", "Unknown error"
            )
            raise RuntimeError(f"Athena query {status}: {error_reason}")

        attempts += 1
        time.sleep(delay)

    raise TimeoutError("Timed out waiting for Athena query to complete")


def run_query(query: str, natural_language_description: str = "") -> dict[str, Any]:
    """Executes SQL query using UNLOAD to S3 and stores path in memory"""
    try:
        # Generate session ID
        session_id = str(uuid.uuid4())

        # Use UNLOAD to export to S3
        athena_output_location = os.environ.get(
            "ATHENA_OUTPUT_LOCATION",
            "s3://actuarial-athena-results-us-east-1/query-results/",
        )
        bucket_path = athena_output_location.split("/query-results/")[0]
        s3_output_path = f"{bucket_path}/unload/{session_id}/"

        # Create UNLOAD query
        unload_query = f"""
        UNLOAD ({query})
        TO '{s3_output_path}'
        WITH (format = 'PARQUET', compression = 'SNAPPY')
        """

        # Execute UNLOAD query
        response = athena.start_query_execution(
            QueryString=unload_query,
            QueryExecutionContext={
                "Database": os.environ.get("ATHENA_DATABASE", "claims_db")
            },
            WorkGroup=os.environ.get("ATHENA_WORKGROUP", "primary"),
            ResultConfiguration={"OutputLocation": athena_output_location},
        )

        query_execution_id = response["QueryExecutionId"]

        # Wait for query completion
        try:
            wait_for_athena_query(athena, query_execution_id)
        except (RuntimeError, TimeoutError) as e:
            return {
                "event_type": "error",
                "session_id": session_id,
                "error": str(e),
                "query_execution_id": query_execution_id,
            }
        # Get row count from S3 files
        s3_path_parts = s3_output_path.replace("s3://", "").split("/", 1)
        bucket = s3_path_parts[0]
        prefix = s3_path_parts[1]

        s3 = boto3.client("s3")
        try:
            objects = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        except Exception as e:
            return {
                "event_type": "error",
                "session_id": session_id,
                "error": f"Failed to list S3 objects: {str(e)}",
            }

        if "Contents" not in objects:
            return {
                "event_type": "error",
                "session_id": session_id,
                "error": "No data files found in S3 output",
            }

        # Read sample file to get metadata
        sample_file = None
        for obj in objects["Contents"]:
            if obj["Size"] > 0:
                sample_file = f"s3://{bucket}/{obj['Key']}"
                break

        if not sample_file:
            return {
                "event_type": "error",
                "session_id": session_id,
                "error": "No valid parquet files found",
            }

        try:
            sample_df = wr.s3.read_parquet(sample_file)
            columns = sample_df.columns.tolist()
            total_files = len([obj for obj in objects["Contents"] if obj["Size"] > 0])
            estimated_rows = len(sample_df) * total_files
        except Exception as e:
            return {
                "event_type": "error",
                "session_id": session_id,
                "error": f"Failed to read parquet file: {str(e)}",
            }

        # Store S3 path in memory
        store_session_metadata(
            session_id=session_id,
            s3_parquet_path=s3_output_path,
            row_count=estimated_rows,
            columns=columns,
            query=query,
        )

        return {
            "event_type": "query_result",
            "session_id": session_id,
            "row_count": estimated_rows,
            "columns": columns,
            "query": query,
            "message": f"Query executed successfully. {estimated_rows} rows available for analysis.",
        }

    except Exception as e:
        return {
            "event_type": "error",
            "session_id": str(uuid.uuid4()),
            "error": f"Query execution failed: {str(e)}",
        }


# Tool registry
TOOLS = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "run_query": run_query,
}


def lambda_handler(event, context):
    """Lambda handler for data query tools"""
    try:
        # Extract tool name from event if not in context (following kbagentcore pattern)
        if not (
            context.client_context
            and hasattr(context.client_context, "custom")
            and context.client_context.custom.get("bedrockAgentCoreToolName")
        ):
            tool_name = None
            if isinstance(event, dict):
                tool_name = (
                    event.get("toolName")
                    or event.get("tool_name")
                    or event.get("bedrockAgentCoreToolName")
                )
                headers = event.get("headers", {})
                if headers:
                    tool_name = tool_name or headers.get("bedrockAgentCoreToolName")

            if tool_name:
                if not hasattr(context, "client_context") or not context.client_context:
                    context.client_context = type("ClientContext", (), {})()
                if not hasattr(context.client_context, "custom"):
                    context.client_context.custom = {}
                context.client_context.custom["bedrockAgentCoreToolName"] = tool_name

        # Get tool name from context
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")

        # Handle prefixed format: data-query-target___list_tables
        if tool_name and "___" in tool_name:
            tool_name = tool_name.split("___")[-1]

        # Parameters are directly in the event root
        parameters = event

        if not tool_name or tool_name not in TOOLS:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": f"Invalid tool: {tool_name}",
                        "available_tools": list(TOOLS.keys()),
                    }
                ),
            }

        result = TOOLS[tool_name](**parameters)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "success": True,
                    "tool": tool_name,
                    "data": result,
                    "timestamp": datetime.now().isoformat(),
                }
            ),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "success": False,
                    "error": str(e),
                    "tool": event.get("tool_name", "unknown"),
                }
            ),
        }
