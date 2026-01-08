import json
import os
from typing import Any

import boto3


def handler(event, context):
    """KB Direct AgentCore Lambda handler for Knowledge Base operations."""

    try:
        # Extract tool name from event if not in context (following actuarial pattern)
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

        # Handle prefixed format: kb-target___ListKnowledgeBases
        if tool_name and "___" in tool_name:
            tool_name = tool_name.split("___")[-1]

        # Parameters are in the event root
        parameters = event

        # Initialize Bedrock clients
        region = os.environ.get("AWS_REGION", "us-east-1")
        bedrock_agent = boto3.client("bedrock-agent", region_name=region)
        bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=region)

        # Route to appropriate tool
        if tool_name == "ListKnowledgeBases":
            return list_knowledge_bases(bedrock_agent)
        elif tool_name == "QueryKnowledgeBases":
            return query_knowledge_bases(bedrock_runtime, parameters)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Unknown tool: {tool_name}"}),
            }

    except Exception as e:
        print(f"Error in KB Direct handler: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def list_knowledge_bases(bedrock_agent) -> dict[str, Any]:
    """List all available Knowledge Bases and their data sources."""

    result = {}

    try:
        # Get all knowledge bases
        paginator = bedrock_agent.get_paginator("list_knowledge_bases")

        for page in paginator.paginate():
            for kb in page.get("knowledgeBaseSummaries", []):
                kb_id = kb.get("knowledgeBaseId")
                kb_name = kb.get("name")
                kb_description = kb.get("description", "")

                # Get data sources for this KB
                data_sources = []
                try:
                    ds_paginator = bedrock_agent.get_paginator("list_data_sources")

                    for ds_page in ds_paginator.paginate(knowledgeBaseId=kb_id):
                        for ds in ds_page.get("dataSourceSummaries", []):
                            data_sources.append(
                                {"id": ds.get("dataSourceId"), "name": ds.get("name")}
                            )
                except Exception as ds_error:
                    print(f"Error getting data sources for KB {kb_id}: {str(ds_error)}")

                result[kb_id] = {
                    "name": kb_name,
                    "description": kb_description,
                    "data_sources": data_sources,
                }

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Failed to list knowledge bases: {str(e)}"}),
        }


def query_knowledge_bases(
    bedrock_runtime, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Query a Knowledge Base with natural language."""

    try:
        # Extract parameters
        query = parameters.get("query") or parameters.get("text")
        knowledge_base_id = (
            parameters.get("knowledge_base_id")
            or parameters.get("knowledgeBaseId")
            or parameters.get("kb_id")
        )
        number_of_results = parameters.get("number_of_results", 10)
        reranking = parameters.get("reranking", False)
        reranking_model_name = parameters.get("reranking_model_name", "AMAZON")
        data_source_ids = parameters.get("data_source_ids")

        if not query or not knowledge_base_id:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {"error": "query and knowledge_base_id are required"}
                ),
            }

        # Build retrieval configuration
        retrieval_config = {
            "vectorSearchConfiguration": {
                "numberOfResults": min(number_of_results, 100)  # Cap at 100
            }
        }

        # Add data source filter if specified
        if data_source_ids and isinstance(data_source_ids, list):
            retrieval_config["vectorSearchConfiguration"]["filter"] = {
                "in": {
                    "key": "x-amz-bedrock-kb-data-source-id",
                    "value": data_source_ids,
                }
            }

        # Add reranking if enabled
        if reranking:
            model_mapping = {
                "COHERE": "cohere.rerank-v3-5:0",
                "AMAZON": "amazon.rerank-v1:0",
            }
            region = bedrock_runtime.meta.region_name
            retrieval_config["vectorSearchConfiguration"]["rerankingConfiguration"] = {
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {
                        "modelArn": f"arn:aws:bedrock:{region}::foundation-model/{model_mapping[reranking_model_name]}"
                    }
                },
            }

        # Execute query
        response = bedrock_runtime.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration=retrieval_config,
        )

        # Format results as newline-separated JSON objects
        documents = []
        for result in response.get("retrievalResults", []):
            location = result.get("location", {})

            # Extract URI from location
            location_uri = (
                location.get("uri", "") if isinstance(location, dict) else str(location)
            )

            # Convert S3 URI to HTTPS URL
            source_url = location_uri
            if location_uri.startswith("s3://"):
                bucket_key = location_uri[5:].split("/", 1)
                if len(bucket_key) == 2:
                    bucket, key = bucket_key
                    region = bedrock_runtime.meta.region_name
                    source_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

            documents.append(
                {
                    "content": result.get("content", {}).get("text", ""),
                    "location": source_url,
                    "score": result.get("score", 0.0),
                }
            )

        # Return as newline-separated JSON objects
        result_lines = [json.dumps(doc) for doc in documents]

        return {"statusCode": 200, "body": "\n".join(result_lines)}

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Failed to query knowledge base: {str(e)}"}),
        }
