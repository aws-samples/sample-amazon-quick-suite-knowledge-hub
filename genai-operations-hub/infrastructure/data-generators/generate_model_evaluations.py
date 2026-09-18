#!/usr/bin/env python3
"""
Generate synthetic Bedrock model evaluation results.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
NUM_EVALUATIONS = 100
OUTPUT_FILE = Path(__file__).parent.parent / "sample-data" / "model-evaluations.csv"

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
    "document-summarizer"
]

PROMPT_VERSIONS = ["v1.0", "v1.1", "v1.2", "v2.0", "v2.1"]

EVALUATION_TYPES = ["automatic", "llm_as_judge", "rag_evaluation"]

def generate_evaluations():
    """Generate synthetic model evaluation data."""
    
    data = []
    start_date = datetime.now() - timedelta(days=90)
    
    for i in range(1, NUM_EVALUATIONS + 1):
        eval_date = start_date + timedelta(days=random.randint(0, 90))
        
        app = random.choice(APPLICATIONS)
        model = random.choice(MODELS)
        prompt_version = random.choice(PROMPT_VERSIONS)
        eval_type = random.choice(EVALUATION_TYPES)
        
        # Base scores vary by model
        if "claude-3-5-sonnet" in model:
            base_score = random.uniform(0.80, 0.95)
        elif "claude-3-opus" in model:
            base_score = random.uniform(0.75, 0.90)
        elif "titan" in model:
            base_score = random.uniform(0.70, 0.85)
        else:  # llama
            base_score = random.uniform(0.72, 0.87)
        
        # Automatic evaluation metrics
        correctness = round(base_score + random.uniform(-0.05, 0.05), 3)
        completeness = round(base_score + random.uniform(-0.08, 0.03), 3)
        helpfulness = round(base_score + random.uniform(-0.03, 0.05), 3)
        harmfulness = round(random.uniform(0.01, 0.10), 3)  # Lower is better
        
        # LLM-as-judge scores (1-5 scale)
        if eval_type == "llm_as_judge":
            judge_score = round(base_score * 5, 1)
            judge_explanation = f"Model demonstrates strong performance with {prompt_version}"
        else:
            judge_score = None
            judge_explanation = None
        
        # RAG-specific metrics
        if eval_type == "rag_evaluation" and app == "document-summarizer":
            faithfulness = round(base_score + random.uniform(-0.05, 0.05), 3)
            relevance = round(base_score + random.uniform(-0.03, 0.05), 3)
            context_precision = round(base_score + random.uniform(-0.08, 0.02), 3)
        else:
            faithfulness = None
            relevance = None
            context_precision = None
        
        # Sample count
        sample_count = random.choice([50, 100, 200, 500])
        
        data.append({
            "evaluation_id": f"EVAL-{i:04d}",
            "date": eval_date.strftime("%Y-%m-%d"),
            "application": app,
            "model_id": model,
            "prompt_version": prompt_version,
            "evaluation_type": eval_type,
            "sample_count": sample_count,
            "correctness": correctness,
            "completeness": completeness,
            "helpfulness": helpfulness,
            "harmfulness": harmfulness,
            "judge_score": judge_score if judge_score else "",
            "judge_explanation": judge_explanation if judge_explanation else "",
            "faithfulness": faithfulness if faithfulness else "",
            "relevance": relevance if relevance else "",
            "context_precision": context_precision if context_precision else ""
        })
    
    return data

def main():
    """Generate and save model evaluation data."""
    print(f"Generating Bedrock model evaluation results...")
    print(f"Evaluations: {NUM_EVALUATIONS}")
    
    data = generate_evaluations()
    
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
