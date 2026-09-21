#!/usr/bin/env python3
"""
Generate a synthetic 5,000-row employee dataset for the Amazon Quick Security Blog.

This script creates:
1. employee_data.csv - Full dataset (30 columns, 5000 rows)
2. employee_data_manager.csv - Manager dataset (sensitive columns removed)
3. employee_data_aggregated.csv - Aggregated by Department x Location (25 rows)
4. rls_rules.csv - Row-Level Security rules template

Usage:
    python generate_employee_data.py [--output-dir ../datasets] [--rows 5000]
"""

import argparse
import csv
import os
import random
from datetime import datetime, timedelta

# Configuration
DEPARTMENTS = ["Sales", "Engineering", "Operations", "Finance", "HR"]
LOCATIONS = ["New York", "San Francisco", "Chicago", "Austin", "Remote"]
JOB_ROLES = {
    "Sales": [
        "Account Manager",
        "Sales Manager",
        "Sales Director",
        "Business Development",
        "Sales Enterprise Rep",
    ],
    "Engineering": [
        "Software Engineer",
        "DevOps Engineer",
        "Architecture Lead",
        "Technical Lead",
        "Data Scientist",
    ],
    "Operations": [
        "Operations Manager",
        "Project Manager",
        "Supply Chain Analyst",
        "Logistics Coordinator",
        "Process Engineer",
    ],
    "Finance": [
        "Accountant",
        "Financial Analyst",
        "Controller",
        "Treasury Analyst",
        "Finance Manager",
    ],
    "HR": [
        "HR Manager",
        "Talent Acquisition",
        "HR Director",
        "HR Business Partner",
        "Compensation Analyst",
    ],
}
POSITION_LEVELS = ["L1", "L2", "L3", "L4", "L5", "L6", "L7"]
GENDERS = ["Male", "Female", "Non-Binary"]
TERMINATION_REASONS = [
    "Voluntary - Better Opportunity",
    "Voluntary - Relocation",
    "Voluntary - Career Change",
    "Involuntary - Performance",
    "Involuntary - Restructuring",
    "Retirement",
]


def generate_employee_id(index):
    return f"EMP{index:06d}"


def random_date(start_year, end_year):
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    random_days = random.randint(0, delta.days)
    return start + timedelta(days=random_days)


def generate_employees(num_rows=5000, seed=42):
    random.seed(seed)
    employees = []

    for i in range(1, num_rows + 1):
        department = random.choice(DEPARTMENTS)
        job_role = random.choice(JOB_ROLES[department])
        position_level = random.choices(
            POSITION_LEVELS, weights=[5, 15, 30, 25, 15, 7, 3], k=1
        )[0]
        location = random.choice(LOCATIONS)
        age = random.randint(22, 62)
        hire_date = random_date(2015, 2025)
        years_at_company = (datetime(2026, 1, 1) - hire_date).days / 365.25
        years_at_company = round(max(0.5, years_at_company), 1)
        years_in_role = round(random.uniform(0.5, min(years_at_company, 8)), 1)

        # Salary based on position level
        level_salary_ranges = {
            "L1": (55000, 75000),
            "L2": (70000, 95000),
            "L3": (85000, 125000),
            "L4": (110000, 155000),
            "L5": (140000, 185000),
            "L6": (170000, 210000),
            "L7": (195000, 220000),
        }
        salary_range = level_salary_ranges[position_level]
        annual_salary = random.randint(salary_range[0], salary_range[1])
        bonus_percent = round(random.uniform(5, 25), 1)

        performance_rating = random.choices(
            [1, 2, 3, 4, 5], weights=[3, 10, 35, 35, 17], k=1
        )[0]
        productivity_index = round(random.uniform(40, 100), 1)
        quality_score = round(random.uniform(50, 100), 1)
        engagement_score = random.randint(30, 100)
        satisfaction_score = random.randint(30, 100)
        work_life_balance = random.randint(1, 5)
        training_hours = random.randint(0, 120)
        certification_count = random.randint(0, 8)
        promoted_last_3 = random.choices(["Yes", "No"], weights=[20, 80], k=1)[0]
        special_projects = random.randint(0, 5)
        absence_days = random.randint(0, 25)

        # Attrition flag - ~25% high
        attrition_factors = 0
        if engagement_score < 50:
            attrition_factors += 1
        if satisfaction_score < 50:
            attrition_factors += 1
        if work_life_balance <= 2:
            attrition_factors += 1
        if performance_rating <= 2:
            attrition_factors += 1
        if years_in_role > 5 and promoted_last_3 == "No":
            attrition_factors += 1

        attrition_flag = (
            "High" if attrition_factors >= 2 or random.random() < 0.15 else "Low"
        )

        # Termination info (only for inactive employees ~10%)
        is_active = random.choices([True, False], weights=[90, 10], k=1)[0]
        termination_date = ""
        termination_reason = ""
        if not is_active:
            termination_date = random_date(2023, 2025).strftime("%Y-%m-%d")
            termination_reason = random.choice(TERMINATION_REASONS)
            attrition_flag = "High"

        # Manager ID (random existing employee, or blank for L6+)
        manager_id = ""
        if position_level not in ["L6", "L7"]:
            manager_id = generate_employee_id(random.randint(1, min(i, num_rows)))

        tenure_bucket = (
            "0-1 years"
            if years_at_company <= 1
            else "1-3 years"
            if years_at_company <= 3
            else "3-5 years"
            if years_at_company <= 5
            else "5-10 years"
            if years_at_company <= 10
            else "10+ years"
        )

        employee = {
            "Employee ID": generate_employee_id(i),
            "Gender": random.choice(GENDERS),
            "Age": age,
            "Department": department,
            "Job Role": job_role,
            "Position Level": position_level,
            "Location": location,
            "Manager ID": manager_id,
            "Hire Date": hire_date.strftime("%Y-%m-%d"),
            "Years At Company": years_at_company,
            "Years In Role": years_in_role,
            "Annual Salary": annual_salary,
            "Bonus Percent": bonus_percent,
            "Performance Rating": performance_rating,
            "Productivity Index": productivity_index,
            "Quality Score": quality_score,
            "Engagement Score": engagement_score,
            "Satisfaction Score": satisfaction_score,
            "Work Life Balance Score": work_life_balance,
            "Training Hours": training_hours,
            "Certification Count": certification_count,
            "Promoted Last 3 Years Description": promoted_last_3,
            "Special Projects Count": special_projects,
            "Absence Days": absence_days,
            "Attrition Flag": attrition_flag,
            "Termination Date": termination_date,
            "Termination Reason": termination_reason,
            "Active Employee Flag": "Yes" if is_active else "No",
            "Tenure Bucket": tenure_bucket,
            "Inactive Employee Flag": "No" if is_active else "Yes",
            "PromotionLast3Yrs": 1 if promoted_last_3 == "Yes" else 0,
        }
        employees.append(employee)

    return employees


def create_manager_dataset(employees):
    """Remove sensitive columns: Annual Salary, Bonus Percent, Termination Date, Termination Reason."""
    sensitive_columns = [
        "Annual Salary",
        "Bonus Percent",
        "Termination Date",
        "Termination Reason",
    ]
    manager_data = []
    for emp in employees:
        row = {k: v for k, v in emp.items() if k not in sensitive_columns}
        manager_data.append(row)
    return manager_data


def create_aggregated_dataset(employees):
    """Aggregate by Department x Location: count, avg engagement, avg satisfaction."""
    aggregation = {}
    for emp in employees:
        key = (emp["Department"], emp["Location"])
        if key not in aggregation:
            aggregation[key] = {
                "count": 0,
                "engagement_sum": 0,
                "satisfaction_sum": 0,
                "training_sum": 0,
            }
        aggregation[key]["count"] += 1
        aggregation[key]["engagement_sum"] += emp["Engagement Score"]
        aggregation[key]["satisfaction_sum"] += emp["Satisfaction Score"]
        aggregation[key]["training_sum"] += emp["Training Hours"]

    aggregated = []
    for (dept, loc), data in sorted(aggregation.items()):
        aggregated.append(
            {
                "Department": dept,
                "Location": loc,
                "Employee Count": data["count"],
                "Avg Engagement Score": round(
                    data["engagement_sum"] / data["count"], 1
                ),
                "Avg Satisfaction Score": round(
                    data["satisfaction_sum"] / data["count"], 1
                ),
                "Avg Training Hours": round(data["training_sum"] / data["count"], 1),
            }
        )
    return aggregated


def create_rls_rules(departments):
    """Create RLS rules template with placeholder usernames."""
    rules = []
    # HR admin gets all departments
    for dept in departments:
        rules.append({"UserName": "Admin/hr-admin-Isengard", "Department": dept})

    # One manager per department
    for dept in departments:
        username = f"Admin/{dept.lower()}-manager-Isengard"
        rules.append({"UserName": username, "Department": dept})

    return rules


def write_csv(filepath, data, fieldnames=None):
    if not data:
        return
    if fieldnames is None:
        fieldnames = list(data[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"  Created: {filepath} ({len(data)} rows, {len(fieldnames)} columns)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic employee data for Amazon Quick Security Blog"
    )
    parser.add_argument(
        "--output-dir", default="../datasets", help="Output directory for CSV files"
    )
    parser.add_argument(
        "--rows", type=int, default=5000, help="Number of employee rows to generate"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )
    args = parser.parse_args()

    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), args.output_dir
    )
    os.makedirs(output_dir, exist_ok=True)

    print(f"Generating {args.rows} employee records (seed={args.seed})...")
    employees = generate_employees(num_rows=args.rows, seed=args.seed)

    # Full dataset
    print("\n1. Full Employee Dataset (HR Leadership):")
    write_csv(os.path.join(output_dir, "employee_data.csv"), employees)

    # Manager dataset (sensitive columns removed)
    print("\n2. Manager Dataset (sensitive columns removed):")
    manager_data = create_manager_dataset(employees)
    write_csv(os.path.join(output_dir, "employee_data_manager.csv"), manager_data)

    # Aggregated dataset
    print("\n3. Aggregated Dataset (Department x Location):")
    aggregated = create_aggregated_dataset(employees)
    write_csv(os.path.join(output_dir, "employee_data_aggregated.csv"), aggregated)

    # RLS rules
    print("\n4. RLS Rules Template:")
    rls_rules = create_rls_rules(DEPARTMENTS)
    write_csv(os.path.join(output_dir, "rls_rules.csv"), rls_rules)

    # Summary
    active_count = sum(1 for e in employees if e["Active Employee Flag"] == "Yes")
    high_attrition = sum(1 for e in employees if e["Attrition Flag"] == "High")
    print("\n--- Summary ---")
    print(f"Total employees: {len(employees)}")
    print(f"Active employees: {active_count}")
    print(f"Inactive employees: {len(employees) - active_count}")
    print(
        f"High attrition risk: {high_attrition} ({high_attrition / len(employees) * 100:.1f}%)"
    )
    print(f"Departments: {', '.join(DEPARTMENTS)}")
    print(f"Locations: {', '.join(LOCATIONS)}")
    print(f"\nAll files saved to: {output_dir}")


if __name__ == "__main__":
    main()
