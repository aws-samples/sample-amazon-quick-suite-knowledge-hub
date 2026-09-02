#!/usr/bin/env python3
"""
Create the manager dataset by removing sensitive columns from the full employee dataset.

Removes: Annual Salary, Bonus Percent, Termination Date, Termination Reason

Usage:
    python create_manager_dataset.py
"""

import csv
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASETS_DIR = os.path.join(SCRIPT_DIR, "..", "datasets")

SENSITIVE_COLUMNS = [
    "Annual Salary",
    "Bonus Percent",
    "Termination Date",
    "Termination Reason",
]

INPUT_FILE = os.path.join(DATASETS_DIR, "employee_data.csv")
OUTPUT_FILE = os.path.join(DATASETS_DIR, "employee_data_manager.csv")


def main():
    with open(INPUT_FILE, newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        fieldnames = [f for f in reader.fieldnames if f not in SENSITIVE_COLUMNS]

        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            count = 0
            for row in reader:
                filtered_row = {
                    k: v for k, v in row.items() if k not in SENSITIVE_COLUMNS
                }
                writer.writerow(filtered_row)
                count += 1

    print(f"Manager dataset created: {OUTPUT_FILE}")
    print(f"  Rows: {count}")
    print(f"  Columns: {len(fieldnames)} (removed: {', '.join(SENSITIVE_COLUMNS)})")


if __name__ == "__main__":
    main()
