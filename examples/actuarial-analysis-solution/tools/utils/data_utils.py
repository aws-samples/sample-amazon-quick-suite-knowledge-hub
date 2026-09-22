import json
import logging
import os
from datetime import datetime
from typing import Any

import awswrangler as wr
import boto3
import pandas as pd

from .constants import AWS_CONFIG, FIELD_MAPPINGS

# Set up logging
# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def load_session_data(session_id: str) -> pd.DataFrame:
    """
    Load parquet data from session S3 location.
    Returns DataFrame in the format expected by actuarial tools.
    """
    try:
        # Get session metadata from AgentCore memory
        session_info = get_session_from_memory(session_id)

        if not session_info:
            raise ValueError(f"No session data found for session_id: {session_id}")

        # Get S3 parquet path
        s3_path = session_info.get("s3_parquet_path")
        if not s3_path:
            # Fallback to dataframe if no S3 path (for backward compatibility)
            dataframe_records = session_info.get("dataframe", [])
            if dataframe_records:
                return pd.DataFrame(dataframe_records)
            else:
                raise ValueError(
                    f"No s3_parquet_path or dataframe found for session_id: {session_id}"
                )

        # Read parquet directly from S3
        df = wr.s3.read_parquet(s3_path)
        return df

    except Exception as e:
        raise ValueError(f"Error loading session data: {e}") from e


def get_session_from_memory(session_id: str) -> dict[str, Any] | None:
    """
    Get session metadata from AgentCore memory.
    Returns the session info dict or None if not found.
    """
    try:
        # Get environment variables
        AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID")
        ACTOR_ID = os.environ.get("ACTOR_ID", "ActuarialAgent")

        if not AGENTCORE_MEMORY_ID:
            return None

        # Query AgentCore memory for session events
        region = os.environ.get("AWS_REGION", "us-east-1")
        agentcore = boto3.client("bedrock-agentcore", region_name=region)

        response = agentcore.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            actorId=ACTOR_ID,
            sessionId=session_id,
            maxResults=AWS_CONFIG["MAX_RESULTS"],
        )

        events = response.get("events", [])
        print(f"Found {len(events)} events for session {session_id}")

        # Look for query result events
        for i, event_item in enumerate(events):
            try:
                if hasattr(event_item, "messages") and event_item.messages:
                    message_content = (
                        event_item.messages[0][0]
                        if isinstance(event_item.messages[0], tuple)
                        else event_item.messages[0]
                    )
                    event_content = (
                        json.loads(message_content)
                        if isinstance(message_content, str)
                        else message_content
                    )
                elif isinstance(event_item, dict) and "payload" in event_item:
                    # Try payload structure
                    payload = event_item["payload"]
                    if isinstance(payload, list) and len(payload) > 0:
                        blob_data = (
                            payload[0].get("blob")
                            if isinstance(payload[0], dict)
                            else payload[0]
                        )
                        event_content = (
                            json.loads(blob_data)
                            if isinstance(blob_data, str)
                            else blob_data
                        )
                    else:
                        event_content = payload
                else:
                    print(f"Event {i} structure not recognized")
                    continue

                print(
                    f"Event {i} content keys: {list(event_content.keys()) if isinstance(event_content, dict) else 'Not a dict'}"
                )

                # Check for query result events
                if event_content and isinstance(event_content, dict):
                    event_type = event_content.get("event_type")
                    print(f"Event {i} type: {event_type}")
                    if event_type == "query_result":
                        print(
                            f"Found query result event with keys: {list(event_content.keys())}"
                        )
                        return event_content
            except Exception as parse_error:
                print(f"Error parsing event {i}: {parse_error}")
                continue

        print(f"No query result found for session_id: {session_id}")
        return None

    except Exception as e:
        print(f"Error querying AgentCore memory: {e}")
        return None


def store_session_metadata(
    session_id: str,
    s3_parquet_path: str,
    row_count: int,
    columns: list,
    query: str = "",
) -> bool:
    """
    Store session metadata (S3 path, not data) in AgentCore memory.
    Returns True if successful, False otherwise.
    """
    try:
        AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID")
        ACTOR_ID = os.environ.get("ACTOR_ID", "ActuarialAgent")

        if not AGENTCORE_MEMORY_ID:
            print("Warning: AGENTCORE_MEMORY_ID not set")
            return False

        # Store lightweight metadata
        session_metadata = {
            "event_type": "query_result",
            "session_id": session_id,
            "s3_parquet_path": s3_parquet_path,
            "row_count": row_count,
            "columns": columns,
            "query": query,
            "timestamp": datetime.now().isoformat(),
        }

        region = os.environ.get("AWS_REGION", "us-east-1")
        agentcore = boto3.client("bedrock-agentcore", region_name=region)

        agentcore.create_event(
            memoryId=AGENTCORE_MEMORY_ID,
            actorId=ACTOR_ID,
            sessionId=session_id,
            eventTimestamp=datetime.now(),
            payload=[{"blob": json.dumps(session_metadata)}],
        )

        print(
            f"Stored session metadata for {session_id}: {row_count} rows at {s3_parquet_path}"
        )
        return True

    except Exception as e:
        print(f"Error storing session metadata: {e}")
        return False


def validate_required_columns(
    df: pd.DataFrame, required_cols: list[str] = None
) -> bool:
    """Validate that DataFrame has required columns for actuarial analysis."""
    if required_cols is None:
        required_cols = FIELD_MAPPINGS["REQUIRED_COLUMNS"]

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"Missing required columns: {missing_cols}")
        return False
    return True


def standardize_date_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize date fields to datetime format."""
    df_copy = df.copy()
    for date_field in FIELD_MAPPINGS["DATE_FIELDS"]:
        if date_field in df_copy.columns:
            df_copy[date_field] = pd.to_datetime(df_copy[date_field], errors="coerce")
    return df_copy


def standardize_amount_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize amount fields to numeric format."""
    df_copy = df.copy()
    for amount_field in FIELD_MAPPINGS["AMOUNT_FIELDS"]:
        if amount_field in df_copy.columns:
            df_copy[amount_field] = pd.to_numeric(
                df_copy[amount_field], errors="coerce"
            ).fillna(0)
    return df_copy


def get_claim_text_fields(claim: pd.Series) -> str:
    """Extract and combine all text fields from a claim for analysis."""
    text_fields = [
        "note_text",
        "lossdescription",
        "injurydescription",
        "claim_notes",
        "description",
    ]
    text_parts = []

    for field in text_fields:
        if field in claim and pd.notna(claim[field]):
            text_parts.append(str(claim[field]))

    return " ".join(text_parts).lower()
