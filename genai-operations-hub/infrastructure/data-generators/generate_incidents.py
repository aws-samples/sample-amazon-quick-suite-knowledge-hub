#!/usr/bin/env python3
"""
Generate synthetic incident log data.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
NUM_INCIDENTS = 25
OUTPUT_FILE = Path(__file__).parent.parent / "sample-data" / "incidents.csv"

APPLICATIONS = [
    "customer-support-chatbot",
    "content-generation-api",
    "code-assistant",
    "document-summarizer",
    "sentiment-analyzer"
]

INCIDENT_TYPES = [
    "quality_degradation",
    "cost_spike",
    "guardrail_failure",
    "latency_increase",
    "error_rate_spike"
]

SEVERITIES = ["Critical", "High", "Medium", "Low"]

ROOT_CAUSES = {
    "quality_degradation": [
        "Prompt template change introduced regression",
        "Model version update affected accuracy",
        "Knowledge base content became stale",
        "RAG retrieval returning irrelevant context"
    ],
    "cost_spike": [
        "Unexpected traffic surge from new feature",
        "Inefficient prompt causing excessive tokens",
        "Provisioned throughput not scaled down",
        "Retry logic causing duplicate invocations"
    ],
    "guardrail_failure": [
        "Guardrail policy misconfiguration",
        "PII detection false negatives",
        "Content filter bypass discovered",
        "Guardrail service timeout"
    ],
    "latency_increase": [
        "Model endpoint throttling",
        "Knowledge base query performance degradation",
        "Network connectivity issues",
        "Increased prompt complexity"
    ],
    "error_rate_spike": [
        "Model endpoint availability issue",
        "Invalid request format after code change",
        "Rate limit exceeded",
        "Timeout configuration too aggressive"
    ]
}

def generate_incidents():
    """Generate synthetic incident data."""
    
    data = []
    start_date = datetime.now() - timedelta(days=90)
    
    for i in range(1, NUM_INCIDENTS + 1):
        incident_date = start_date + timedelta(days=random.randint(0, 90))
        
        app = random.choice(APPLICATIONS)
        incident_type = random.choice(INCIDENT_TYPES)
        severity = random.choices(
            SEVERITIES,
            weights=[0.1, 0.3, 0.4, 0.2]
        )[0]
        
        root_cause = random.choice(ROOT_CAUSES[incident_type])
        
        # Resolution time based on severity
        if severity == "Critical":
            resolution_hours = round(random.uniform(0.5, 4), 1)
        elif severity == "High":
            resolution_hours = round(random.uniform(2, 12), 1)
        elif severity == "Medium":
            resolution_hours = round(random.uniform(4, 24), 1)
        else:
            resolution_hours = round(random.uniform(12, 72), 1)
        
        # Status
        if (datetime.now() - incident_date).days > 7:
            status = "Resolved"
        else:
            status = random.choice(["Open", "Investigating", "Resolved"])
        
        # Impact description
        if incident_type == "quality_degradation":
            impact = f"User satisfaction dropped by {random.randint(10, 30)}%"
        elif incident_type == "cost_spike":
            impact = f"Daily cost increased by ${random.randint(100, 1000)}"
        elif incident_type == "guardrail_failure":
            impact = f"{random.randint(5, 50)} policy violations missed"
        elif incident_type == "latency_increase":
            impact = f"P99 latency increased by {random.randint(200, 2000)}ms"
        else:
            impact = f"Error rate increased to {random.randint(5, 25)}%"
        
        data.append({
            "incident_id": f"INC-{i:04d}",
            "date": incident_date.strftime("%Y-%m-%d"),
            "application": app,
            "incident_type": incident_type,
            "severity": severity,
            "status": status,
            "root_cause": root_cause,
            "impact": impact,
            "resolution_hours": resolution_hours if status == "Resolved" else "",
            "action_taken": "See incident report" if status == "Resolved" else "Investigation ongoing"
        })
    
    return data

def main():
    """Generate and save incident data."""
    print(f"Generating incident log data...")
    print(f"Incidents: {NUM_INCIDENTS}")
    
    data = generate_incidents()
    
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
