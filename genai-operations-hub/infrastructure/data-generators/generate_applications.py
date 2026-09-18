#!/usr/bin/env python3
"""
Generate synthetic application metadata.
"""

import csv
from pathlib import Path

# Configuration
OUTPUT_FILE = Path(__file__).parent.parent / "sample-data" / "applications.csv"

APPLICATIONS = [
    {
        "application_id": "APP-001",
        "application_name": "customer-support-chatbot",
        "owner": "support-team@company.com",
        "stage": "prod",
        "primary_model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "guardrail_id": "gr-12345",
        "knowledge_base_id": "kb-67890",
        "avg_daily_users": 5000,
        "created_date": "2024-06-15"
    },
    {
        "application_id": "APP-002",
        "application_name": "content-generation-api",
        "owner": "marketing-team@company.com",
        "stage": "prod",
        "primary_model": "anthropic.claude-3-opus-20240229-v1:0",
        "guardrail_id": "gr-12346",
        "knowledge_base_id": "",
        "avg_daily_users": 200,
        "created_date": "2024-07-01"
    },
    {
        "application_id": "APP-003",
        "application_name": "code-assistant",
        "owner": "engineering-team@company.com",
        "stage": "prod",
        "primary_model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "guardrail_id": "",
        "knowledge_base_id": "kb-67891",
        "avg_daily_users": 150,
        "created_date": "2024-08-10"
    },
    {
        "application_id": "APP-004",
        "application_name": "document-summarizer",
        "owner": "operations-team@company.com",
        "stage": "prod",
        "primary_model": "amazon.titan-text-premier-v1:0",
        "guardrail_id": "gr-12347",
        "knowledge_base_id": "kb-67892",
        "avg_daily_users": 800,
        "created_date": "2024-09-05"
    },
    {
        "application_id": "APP-005",
        "application_name": "sentiment-analyzer",
        "owner": "analytics-team@company.com",
        "stage": "prod",
        "primary_model": "meta.llama3-70b-instruct-v1:0",
        "guardrail_id": "",
        "knowledge_base_id": "",
        "avg_daily_users": 300,
        "created_date": "2024-10-20"
    }
]

def main():
    """Generate and save application metadata."""
    print(f"Generating application metadata...")
    print(f"Applications: {len(APPLICATIONS)}")
    
    # Write to CSV
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=APPLICATIONS[0].keys())
        writer.writeheader()
        writer.writerows(APPLICATIONS)
    
    print(f"✅ Generated {len(APPLICATIONS)} records")
    print(f"📁 Saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
