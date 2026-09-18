#!/usr/bin/env python3
"""
Generate synthetic Bedrock cost and usage data.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
DAYS = 90
OUTPUT_FILE = Path(__file__).parent.parent / "sample-data" / "cost-usage.csv"

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

# Pricing per 1K tokens (approximate)
MODEL_PRICING = {
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {"input": 0.003, "output": 0.015},
    "anthropic.claude-3-opus-20240229-v1:0": {"input": 0.015, "output": 0.075},
    "amazon.titan-text-premier-v1:0": {"input": 0.0005, "output": 0.0015},
    "meta.llama3-70b-instruct-v1:0": {"input": 0.00195, "output": 0.00256}
}

def generate_cost_usage():
    """Generate synthetic cost and usage data."""
    
    data = []
    start_date = datetime.now() - timedelta(days=DAYS)
    
    for day in range(DAYS):
        current_date = start_date + timedelta(days=day)
        
        for app in APPLICATIONS:
            for model in MODELS:
                # Daily token usage
                daily_invocations = random.randint(1000, 10000)
                input_tokens = daily_invocations * random.randint(200, 1500)
                output_tokens = daily_invocations * random.randint(100, 800)
                
                # Calculate costs
                pricing = MODEL_PRICING[model]
                input_cost = (input_tokens / 1000) * pricing["input"]
                output_cost = (output_tokens / 1000) * pricing["output"]
                total_cost = input_cost + output_cost
                
                # Guardrail costs (if applicable)
                guardrail_evaluations = int(daily_invocations * random.uniform(0.3, 0.7))
                guardrail_cost = guardrail_evaluations * 0.00075  # $0.75 per 1K evaluations
                
                # Provisioned throughput (some apps use it)
                if app in ["customer-support-chatbot", "content-generation-api"] and random.random() < 0.3:
                    provisioned_throughput_hours = 24
                    provisioned_cost = 24 * 8.00  # $8/hour for model units
                else:
                    provisioned_throughput_hours = 0
                    provisioned_cost = 0
                
                total_daily_cost = total_cost + guardrail_cost + provisioned_cost
                
                data.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "application": app,
                    "model_id": model,
                    "invocation_count": daily_invocations,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "input_cost_usd": round(input_cost, 4),
                    "output_cost_usd": round(output_cost, 4),
                    "guardrail_evaluations": guardrail_evaluations,
                    "guardrail_cost_usd": round(guardrail_cost, 4),
                    "provisioned_throughput_hours": provisioned_throughput_hours,
                    "provisioned_cost_usd": round(provisioned_cost, 2),
                    "total_cost_usd": round(total_daily_cost, 2)
                })
    
    return data

def main():
    """Generate and save cost and usage data."""
    print(f"Generating Bedrock cost and usage data...")
    print(f"Days: {DAYS}")
    
    data = generate_cost_usage()
    
    # Write to CSV
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    
    print(f"✅ Generated {len(data)} records")
    print(f"📁 Saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
