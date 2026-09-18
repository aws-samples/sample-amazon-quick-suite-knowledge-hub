"""
AgentCore Lambda Handler
=======================
Main Lambda function handler for actuarial analysis tools integration with AWS Bedrock AgentCore.

Key Features:
- Routes tool requests to appropriate actuarial analysis modules
- Manages session data storage and retrieval from AgentCore memory
- Handles data extraction, fraud detection, litigation analysis, risk analysis, loss reserving, and monitoring
- Integrates with AWS Athena for data querying and S3 for data storage
- Provides unified API interface for all actuarial tools

Supported Tools:
- detect_litigation: Litigation indicator analysis
- score_fraud_risk: Fraud probability scoring
- analyze_risk_factors: Risk segmentation and analysis
- build_loss_triangles: Loss development triangle construction
- calculate_reserves: IBNR reserve calculations
- monitor_development: KPI monitoring and alerts
"""

import json
import logging
import os
from datetime import datetime

import boto3

# Import actuarial analysis modules
import fraud_detection
import litigation_analysis
import loss_reserving
import monitoring
import risk_analysis
from utils.data_utils import load_session_data

# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    try:
        logger.info(
            f"AgentCore Lambda invoked with tool: {event.get('tool_name', 'unknown')}"
        )

        memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
        if not memory_id:
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": "AGENTCORE_MEMORY_ID environment variable not set"}
                ),
            }

        tool_name = (
            context.client_context.custom.get("bedrockAgentCoreToolName", "")
            if context.client_context and context.client_context.custom
            else ""
        )
        if "___" in tool_name:
            tool_name = tool_name.split("___")[1]

        body = (
            json.loads(event.get("body", "{}"))
            if isinstance(event.get("body"), str)
            else event
        )

        session_id = body.get("session_id")
        if not session_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "session_id is required"}),
            }

        actor_id = os.environ.get("ACTOR_ID", "ActuarialAgent")
        if context.client_context and context.client_context.custom:
            context_actor_id = context.client_context.custom.get(
                "actorId"
            ) or context.client_context.custom.get("actor_id")
            if context_actor_id:
                actor_id = context_actor_id

        agentcore = boto3.client("bedrock-agentcore", region_name="us-east-1")

        try:
            logger.info(f"Loading session data for session: {session_id}")
            df = load_session_data(session_id)

            if df is not None and not df.empty:
                logger.info(
                    f"Session data loaded successfully: {len(df)} records, {len(df.columns)} columns"
                )
                try:
                    data_event = df.to_dict("records")
                except Exception as convert_error:
                    logger.error(
                        f"Failed to convert DataFrame to records: {str(convert_error)}"
                    )
                    return {
                        "statusCode": 500,
                        "body": json.dumps(
                            {
                                "error": f"DataFrame conversion failed: {str(convert_error)}"
                            }
                        ),
                    }
            else:
                return {
                    "statusCode": 404,
                    "body": json.dumps(
                        {"error": f"No data found for session_id: {session_id}"}
                    ),
                }

        except Exception as load_error:
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": f"Failed to load session data: {str(load_error)}"}
                ),
            }
        # Extract optional configuration parameters
        fraud_config = body.get("fraud_config")
        litigation_config = body.get("litigation_config")
        monitoring_config = body.get("monitoring_config")

        if tool_name == "detect_litigation":
            logger.info("Executing litigation detection")
            result = litigation_analysis.detect_litigation(
                data_event, litigation_config
            )
        elif tool_name == "score_fraud_risk":
            logger.info("Executing fraud risk scoring")
            result = fraud_detection.score_fraud_risk(data_event, fraud_config)
        elif tool_name == "analyze_risk_factors":
            logger.info("Executing risk factor analysis")
            result = risk_analysis.analyze_risk_factors(data_event)
        elif tool_name == "build_loss_triangles":
            logger.info("Executing loss triangle construction")
            result = loss_reserving.build_loss_triangles(data_event)
            logger.info(
                f"Triangle construction result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}"
            )

            if "incurred_triangle" in result and session_id:
                logger.info("Storing triangle data in AgentCore memory")
                try:
                    triangle_result = {
                        "event_type": "triangle_result",
                        "session_id": session_id,
                        "incurred_triangle": result.get("incurred_triangle", {}),
                        "paid_triangle": result.get("paid_triangle", {}),
                        "reserve_triangle": result.get("reserve_triangle", {}),
                        "count_triangle": result.get("count_triangle", {}),
                        "triangle_data": result.get("triangle_data", []),
                    }

                    agentcore.create_event(
                        memoryId=memory_id,
                        actorId=actor_id,
                        sessionId=session_id,
                        eventTimestamp=datetime.now(),
                        payload=[{"blob": json.dumps(triangle_result)}],
                    )
                except Exception:
                    pass
        elif tool_name == "calculate_reserves":
            logger.info("=== STARTING CALCULATE_RESERVES ===")
            logger.info("Executing IBNR reserve calculation")
            triangles_data = None

            try:
                logger.info("Looking for triangle data in AgentCore memory")
                response = agentcore.list_events(
                    memoryId=memory_id,
                    actorId=actor_id,
                    sessionId=session_id,
                    maxResults=100,
                )

                events = response.get("events", [])
                logger.info(f"Found {len(events)} events in memory")

                for event_item in events:
                    try:
                        payload_blob = event_item.get("payload", [{}])[0].get(
                            "blob", "{}"
                        )
                        event_data = json.loads(payload_blob)

                        if event_data.get("event_type") == "triangle_result":
                            triangles_data = event_data
                            print("=== FOUND TRIANGLE DATA IN MEMORY ===")
                            if (
                                "incurred_triangle" in triangles_data
                                and "data" in triangles_data["incurred_triangle"]
                            ):
                                sample_data = triangles_data["incurred_triangle"][
                                    "data"
                                ]
                                if sample_data:
                                    first_key = list(sample_data.keys())[0]
                                    columns = (
                                        list(sample_data[first_key].keys())
                                        if sample_data[first_key]
                                        else []
                                    )
                                    print(f"=== MEMORY TRIANGLE COLUMNS: {columns} ===")
                            break
                    except Exception:
                        continue

                if not triangles_data:
                    # Build triangles first
                    triangle_result = loss_reserving.build_loss_triangles(data_event)

                    # Store triangle data for future use
                    triangle_data_to_store = {
                        "event_type": "triangle_result",
                        "session_id": session_id,
                        "incurred_triangle": triangle_result.get(
                            "incurred_triangle", {}
                        ),
                        "paid_triangle": triangle_result.get("paid_triangle", {}),
                        "reserve_triangle": triangle_result.get("reserve_triangle", {}),
                        "count_triangle": triangle_result.get("count_triangle", {}),
                        "triangle_data": triangle_result.get("triangle_data", []),
                    }

                    try:
                        agentcore.create_event(
                            memoryId=memory_id,
                            actorId=actor_id,
                            sessionId=session_id,
                            eventTimestamp=datetime.now(),
                            payload=[{"blob": json.dumps(triangle_data_to_store)}],
                        )
                        print(
                            f"Stored new triangle data in AgentCore memory for session: {session_id}"
                        )
                        triangles_data = triangle_data_to_store
                    except Exception as e:
                        print(f"Warning: Could not store triangle data in memory: {e}")
                        triangles_data = triangle_data_to_store  # Use it anyway

            except Exception as memory_error:
                print(f"Error retrieving triangle data from memory: {memory_error}")

            result = loss_reserving.calculate_reserves(triangles_data)
        elif tool_name == "monitor_development":
            result = monitoring.monitor_development(data_event, monitoring_config)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": f"Unknown tool: {tool_name}",
                        "available_tools": [
                            "detect_litigation",
                            "score_fraud_risk",
                            "analyze_risk_factors",
                            "build_loss_triangles",
                            "calculate_reserves",
                            "monitor_development",
                        ],
                    }
                ),
            }

        # Only store triangle data for calculate_reserves dependency
        # All other tools are independent and don't need memory storage

        return {
            "statusCode": 200,
            "body": json.dumps({"session_id": session_id, "result": result}),
        }

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
