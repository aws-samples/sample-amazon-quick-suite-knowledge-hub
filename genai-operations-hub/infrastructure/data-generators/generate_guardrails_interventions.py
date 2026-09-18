#!/usr/bin/env python3
"""
Generate synthetic Bedrock guardrails intervention data.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
DAYS = 90
OUTPUT_FILE = Path(__file__).parent.parent / "sample-data" / "guardrails-interventions.csv"

APPLICATIONS = [
    "customer-support-chatbot",
    "content-generation-api",
    "code-assistant",
    "document-summarizer",
    "sentiment-analyzer"
]

POLICY_TYPES = [
    "content_filter",
    "denied_topics",
    "pii_redaction",
    "prompt_attack_detection"
]

FINDING_TYPES = {
    "content_filter": ["hate", "violence", "sexual", "insults", "misconduct"],
    "denied_topics": ["financial_advice", "medical_advice", "legal_advice", "political"],
    "pii_redaction": ["email", "phone", "ssn", "credit_card", "address"],
    "prompt_attack_detection": ["jailbreak", "prompt_injection", "role_play_attack"]
}

ACTIONS = ["BLOCKED", "REDACTED", "FLAGGED"]

def generate_interventions():
    """Generate synthetic guardrails intervention data."""
    
    data = []
    start_date = datetime.now() - timedelta(days=DAYS)
    intervention_id = 1
    
    for day in range(DAYS):
        current_date = start_date + timedelta(days=day)
        
        # Variable number of interventions per day (5-30)
        daily_interventions = random.randint(5, 30)
        
        for _ in range(daily_interventions):
            timestamp = current_date + timedelta(
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59)
            )
            
            app = random.choice(APPLICATIONS)
            policy_type = random.choice(POLICY_TYPES)
            finding_type = random.choice(FINDING_TYPES[policy_type])
            
            # Action based on policy type
            if policy_type == "pii_redaction":
                action = "REDACTED"
            elif policy_type == "prompt_attack_detection":
                action = "BLOCKED"
            else:
                action = random.choice(["BLOCKED", "FLAGGED"])
            
            # Confidence score
            confidence = round(random.uniform(0.75, 0.99), 2)
            
            # Latency impact (ms)
            latency_ms = round(random.uniform(50, 300), 1)
            
            # Sample blocked content (sanitized examples)
            if policy_type == "content_filter":
                sample_text = f"[{finding_type.upper()} CONTENT DETECTED]"
            elif policy_type == "pii_redaction":
                sample_text = f"[{finding_type.upper()}: ***REDACTED***]"
            elif policy_type == "prompt_attack_detection":
                sample_text = f"[{finding_type.upper()} ATTEMPT BLOCKED]"
            else:
                sample_text = f"[{finding_type.upper()} TOPIC DENIED]"
            
            data.append({
                "intervention_id": f"INT-{intervention_id:06d}",
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "application": app,
                "policy_type": policy_type,
                "finding_type": finding_type,
                "action": action,
                "confidence_score": confidence,
                "latency_ms": latency_ms,
                "sample_text": sample_text
            })
            
            intervention_id += 1
    
    return data

def main():
    """Generate and save guardrails intervention data."""
    print(f"Generating Bedrock guardrails intervention data...")
    print(f"Days: {DAYS}")
    
    data = generate_interventions()
    
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
