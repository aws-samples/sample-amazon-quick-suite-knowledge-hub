#!/usr/bin/env python3
"""
Generate synthetic Bedrock model invocation logs in JSON format.
Matches the structure from AWS Bedrock invocation logging.
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
DAYS = 90
OUTPUT_DIR = Path(__file__).parent.parent / "sample-data" / "bedrock-logs"
ACCOUNT_ID = "123456789012"
REGION = "us-east-1"

MODELS = [
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-opus-20240229-v1:0",
    "amazon.titan-text-premier-v1:0",
    "meta.llama3-70b-instruct-v1:0"
]

APPLICATIONS = [
    "customer-support-chatbot",
    "content-generation-api",
    "code-assistant",
    "document-summarizer",
    "sentiment-analyzer"
]

IAM_ROLES = {
    "customer-support-chatbot": f"arn:aws:iam::{ACCOUNT_ID}:role/CustomerSupportLambdaRole",
    "content-generation-api": f"arn:aws:iam::{ACCOUNT_ID}:role/ContentGenLambdaRole",
    "code-assistant": f"arn:aws:iam::{ACCOUNT_ID}:role/CodeAssistLambdaRole",
    "document-summarizer": f"arn:aws:iam::{ACCOUNT_ID}:role/DocSummarizerLambdaRole",
    "sentiment-analyzer": f"arn:aws:iam::{ACCOUNT_ID}:role/SentimentLambdaRole"
}

STOP_REASONS = ["end_turn", "max_tokens", "stop_sequence"]

def generate_invocation_log(timestamp, app, model):
    """Generate a single Bedrock invocation log in AWS format."""
    
    # Inference config
    temperature = round(random.uniform(0.5, 1.0), 1)
    top_p = round(random.uniform(0.8, 1.0), 1)
    max_tokens = random.choice([1000, 2000, 4000])
    
    # Token counts
    input_tokens = random.randint(200, 1500)
    output_tokens = random.randint(100, 800)
    
    # Latency varies by model
    if "claude-3-opus" in model:
        latency_ms = random.randint(800, 1500)
    elif "claude-3-5-sonnet" in model:
        latency_ms = random.randint(400, 800)
    elif "titan" in model:
        latency_ms = random.randint(300, 600)
    else:  # llama
        latency_ms = random.randint(500, 900)
    
    stop_reason = random.choices(STOP_REASONS, weights=[0.85, 0.10, 0.05])[0]
    
    log = {
        "schemaType": "ModelInvocationLog",
        "schemaVersion": "1.0",
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "accountId": ACCOUNT_ID,
        "identity": {
            "arn": IAM_ROLES[app]
        },
        "region": REGION,
        "requestId": f"{random.randint(10000000, 99999999)}-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}-{random.randint(100000000000, 999999999999)}",
        "operation": "Converse",
        "modelId": model,
        "input": {
            "inputContentType": "application/json",
            "inputBodyJson": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": f"Sample user prompt for {app}"
                            }
                        ]
                    }
                ],
                "system": [
                    {
                        "text": f"You are an AI assistant for {app}"
                    }
                ],
                "inferenceConfig": {
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                    "topP": top_p
                }
            },
            "inputTokenCount": input_tokens
        },
        "output": {
            "outputContentType": "application/json",
            "outputBodyJson": {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "text": "Sample assistant response"
                            }
                        ]
                    }
                },
                "stopReason": stop_reason,
                "metrics": {
                    "latencyMs": latency_ms
                },
                "usage": {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "totalTokens": input_tokens + output_tokens
                }
            },
            "outputTokenCount": output_tokens
        }
    }
    
    return log

def generate_invocations():
    """Generate synthetic Bedrock invocation logs."""
    
    start_date = datetime.now() - timedelta(days=DAYS)
    
    for day in range(DAYS):
        current_date = start_date + timedelta(days=day)
        
        for hour in range(24):
            timestamp = current_date.replace(hour=hour, minute=0, second=0)
            
            # Create directory structure: AWSLogs/{account}/BedrockModelInvocationLogs/{region}/year/month/day/hour/
            log_dir = OUTPUT_DIR / "AWSLogs" / ACCOUNT_ID / "BedrockModelInvocationLogs" / REGION / timestamp.strftime("%Y/%m/%d/%H")
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate logs for this hour
            logs = []
            for app in APPLICATIONS:
                for model in MODELS:
                    # Variable invocations per hour
                    num_invocations = random.randint(5, 50)
                    
                    for _ in range(num_invocations):
                        log_timestamp = timestamp + timedelta(
                            minutes=random.randint(0, 59),
                            seconds=random.randint(0, 59)
                        )
                        logs.append(generate_invocation_log(log_timestamp, app, model))
            
            # Write logs to file
            log_file = log_dir / f"bedrock-logs-{timestamp.strftime('%Y%m%d-%H%M%S')}.json"
            with open(log_file, 'w') as f:
                for log in logs:
                    f.write(json.dumps(log) + '\n')

def main():
    """Generate and save Bedrock invocation logs."""
    print(f"Generating Bedrock model invocation logs...")
    print(f"Days: {DAYS}")
    print(f"Models: {len(MODELS)}")
    print(f"Applications: {len(APPLICATIONS)}")
    print(f"Output: {OUTPUT_DIR}")
    
    generate_invocations()
    
    # Count total files
    total_files = len(list(OUTPUT_DIR.rglob("*.json")))
    print(f"✅ Generated {total_files} log files")
    print(f"📁 Saved to: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
