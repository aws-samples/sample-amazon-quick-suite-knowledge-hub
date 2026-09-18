#!/usr/bin/env python3
"""
Generate all sample data files for GenAI Operations Hub.
"""

import subprocess
import sys
from pathlib import Path

def run_generator(script_name):
    """Run a data generator script."""
    print(f"\n{'='*60}")
    print(f"Running {script_name}...")
    print('='*60)
    
    result = subprocess.run(
        [sys.executable, script_name],
        cwd=Path(__file__).parent,
        capture_output=False
    )
    
    if result.returncode != 0:
        print(f"❌ Error running {script_name}")
        return False
    
    return True

def main():
    """Generate all sample data."""
    print("🚀 Generating all sample data for GenAI Operations Hub")
    print("="*60)
    
    generators = [
        "generate_model_invocations.py",
        "generate_guardrails_interventions.py",
        "generate_model_evaluations.py",
        "generate_cost_usage.py",
        "generate_applications.py",
        "generate_incidents.py"
    ]
    
    success_count = 0
    for generator in generators:
        if run_generator(generator):
            success_count += 1
    
    print(f"\n{'='*60}")
    print(f"✅ Completed: {success_count}/{len(generators)} generators")
    print('='*60)
    
    # Show file sizes
    print("\n📊 Generated files:")
    data_dir = Path(__file__).parent.parent / "sample-data"
    total_size = 0
    for csv_file in sorted(data_dir.glob("*.csv")):
        size_mb = csv_file.stat().st_size / (1024 * 1024)
        total_size += size_mb
        print(f"  {csv_file.name}: {size_mb:.2f} MB")
    
    print(f"\n  Total: {total_size:.2f} MB")

if __name__ == "__main__":
    main()
