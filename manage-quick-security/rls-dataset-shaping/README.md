---
category: Security
description: "Row-Level Security & Dataset Shaping - Secure Amazon Quick from POC to Production"
---

# Row-Level Security & Dataset Shaping

**Secure Amazon Quick from POC to Production** using dataset shaping, Row-Level Security (RLS), purpose-built Chat Agents, human-in-the-loop Flows, and CloudTrail auditing.

Many organizations build successful AI-powered analytics proofs of concept with Amazon Quick but encounter security gaps when scaling to production. Permissions alone are not enough — a chat agent that connects to a dataset with salary columns can surface that data regardless of dashboard-level restrictions, and a Flow that sends automated notifications without review can leak sensitive information to the wrong audience.

This sample demonstrates a **data-architecture-first approach** to closing those gaps. Using a fictional 5,000-employee company (AnyCompany), we walk through six security controls that scale with adoption.

## Purpose

This solution demonstrates how to:

- **Shape datasets to match authorization** — one source dataset split into three audience-aligned views
- **Apply Row-Level Security (RLS)** — restrict data so users only see rows they're authorized for
- **Configure purpose-built Chat Agents** — each scoped to a single dataset with explicit boundaries
- **Gate outbound actions with Flows** — human-in-the-loop approval before any automated notification
- **Classify documents before upload** — exclude sensitive data from shared Knowledge Bases
- **Audit with CloudTrail** — capture all agent queries, dataset access, and permission changes

The core principle: **security enforced by data architecture (column removal, dataset separation) is structurally stronger than security by permissions alone.**

## Project Structure

```
rls-dataset-shaping/
├── README.md
├── datasets/
│   ├── employee_data.csv              # Full dataset (5,000 rows, 31 columns) - HR Leadership
│   ├── employee_data_manager.csv      # Sensitive columns removed - Department Managers
│   ├── employee_data_aggregated.csv   # Department x Location summaries (25 rows) - All Employees
│   └── rls-rules.csv                  # Row-Level Security rules template
├── documents/
│   ├── employee_handbook.pdf          # General policies (upload to Knowledge Base)
│   ├── leave_policy.pdf               # Leave policies (upload to Knowledge Base)
│   ├── public_holidays.csv            # Holiday reference data (upload to Knowledge Base)
│   ├── onboarding_checklist.pdf       # Process documentation (upload to Knowledge Base)
│   ├── performance_review_guidelines.pdf  # Process documentation (upload to Knowledge Base)
│   └── employee_feedback_full_dataset.pdf # DO NOT UPLOAD - contains individual performance reviews
├── agents/
│   ├── executive-insights-agent.md    # Persona prompt and test queries
│   ├── manager-assistant-agent.md     # Persona prompt and test queries
│   └── employee-self-service-agent.md # Persona prompt and test queries
├── flows/
│   └── weekly-attrition-risk-alert.json  # Flow definition with approval gate
├── scripts/
│   ├── generate_employee_data.py      # Generate synthetic dataset
│   ├── create_manager_dataset.py      # Create manager dataset from full dataset
│   └── deploy_quicksight_resources.sh # AWS CLI deployment helper
└── cloudformation/
    └── quicksight-security-stack.yaml # IaC for groups, data source, and CloudTrail
```

## Prerequisites

- An AWS account with Amazon Quick enabled (Enterprise plan)
- At least three Amazon Quick user accounts representing different personas (or one user that can switch roles)
- AWS CLI v2 configured with appropriate permissions
- AWS CloudTrail trail configured in your account
- AWS Secrets Manager access if your Flows connect to external systems
- Python 3.8+ (only if regenerating datasets)

!!! note
    This walkthrough uses QuickSight-managed identity (non-IDC). If your account uses AWS Identity Center (IDC) for identity federation, group management is handled in the Identity Center console rather than within QuickSight. The security patterns (dataset shaping, RLS, agent isolation) still apply, but group assignment steps will differ.

## Quick Start

### Step 1: Upload Datasets

Upload the three datasets from the `datasets/` folder to Amazon Quick:

| Dataset | File | Audience | Rows |
|---------|------|----------|------|
| anycompany-employees-full | `employee_data.csv` | HR Leadership | 5,000 |
| anycompany-employees-manager | `employee_data_manager.csv` | Department Managers | 5,000 (RLS-filtered) |
| anycompany-employees-aggregated | `employee_data_aggregated.csv` | All Employees | 25 |

Navigate to **Datasets > New dataset > Upload a file** in the Amazon Quick console.

### Step 2: Apply Row-Level Security

1. Upload `datasets/rls-rules.csv` as a new dataset named `anycompany-rls-rules`
2. Open the `anycompany-employees-manager` dataset
3. Go to the **Row-level security** tab
4. Select `anycompany-rls-rules` as the permissions dataset
5. Map `UserName` to the QuickSight username field and `Department` to the Department column

!!! tip "Finding your username"
    Log in > create any analysis > add calculated field `username()` > place on a KPI visual. The displayed string is your RLS username. Use the exact string — even one character mismatch returns zero rows.

**Update `rls-rules.csv`** with your actual QuickSight usernames before uploading.

### Step 3: Create Groups

Create three groups in **Manage Amazon Quick > Manage Groups**:

| Group | Members | Dataset Access |
|-------|---------|----------------|
| hr-leadership | HR admin user | full, manager, aggregated |
| dept-managers | One user per department | manager (RLS-filtered), aggregated |
| all-employees | All other users | aggregated only |

### Step 4: Create a Space and Upload Knowledge Base Documents

1. Navigate to **Spaces > Create Space** and name it `AnyCompany HR`
2. Upload the following documents from the `documents/` folder:

| Document | Upload | Reason |
|----------|--------|--------|
| employee_handbook.pdf | Yes | General policies |
| leave_policy.pdf | Yes | General policies |
| public_holidays.csv | Yes | Reference data, no PII |
| onboarding_checklist.pdf | Yes | Process documentation |
| performance_review_guidelines.pdf | Yes | Process documentation |
| employee_feedback_full_dataset.pdf | **No** | Contains individual performance reviews |

!!! warning "Why exclude employee_feedback_full_dataset.pdf?"
    All documents in a Knowledge Base are queryable by anyone with Viewer access to the Space. The only way to prevent dept-managers from accessing individual performance reviews is to keep the document out entirely.

3. Share the Space:
    - **hr-leadership** → Owner (view, query, upload)
    - **dept-managers** → Viewer (view, query only)
    - **all-employees** → Do not add (no access)

### Step 5: Create Chat Agents

Create three agents in **Chat Agents > Create a chat agent (+ Blank)**. Copy the persona prompts from the `agents/` folder:

1. **Executive Insights Agent** → connects to `employee_data_aggregated` + AnyCompany HR Space
2. **Manager Assistant Agent** → connects to `employee_data_manager` (RLS-enabled) + AnyCompany HR Space
3. **Employee Self-Service Agent** → connects to AnyCompany HR Space

See `agents/*.md` for the full persona prompts and test queries.

### Step 6: Verify Agent Boundaries with Adversarial Queries

Test each agent with queries that should succeed and queries that should be refused. See the test tables in each agent's markdown file under `agents/`.

### Step 7: Create the Weekly Attrition Risk Alert Flow

1. Navigate to **Flows > Create Flow**
2. Name: `Weekly Attrition Risk Alert`
3. Connect to the AnyCompany HR Space
4. Configure four steps (see `flows/weekly-attrition-risk-alert.json` for details):
    - Retrieve High Attrition Employees (filter Attrition Flag = High)
    - Analyze Attrition Risk Factors
    - **Human Approval Process** (mandatory gate)
    - Manager Notification
5. Set trigger to weekly schedule

### Step 8: Enable CloudTrail

Navigate to **AWS CloudTrail > Event history** and verify events are captured with Event source: `quicksight.amazonaws.com`.

## Deployment (Optional)

For automated setup of groups and S3 resources:

<!-- markdownlint-disable MD046 -->
=== "Shell Script"

    ```bash
    cd scripts/
    ./deploy_quicksight_resources.sh \
        --account-id 123456789012 \
        --bucket my-quicksight-datasets \
        --dry-run
    ```

=== "CloudFormation"

    ```bash
    aws cloudformation deploy \
        --template-file cloudformation/quicksight-security-stack.yaml \
        --stack-name quicksight-security-blog \
        --parameter-overrides \
            AwsAccountId=123456789012 \
            S3BucketName=my-quicksight-datasets \
            HRAdminUsername="Admin/hr-admin-Isengard"
    ```
<!-- markdownlint-enable MD046 -->

## Regenerating Datasets

If you want to customize the synthetic data:

```bash
cd scripts/

# Generate fresh 5,000-row dataset
python3 generate_employee_data.py --output-dir ../datasets --rows 5000

# Create the manager dataset (removes sensitive columns)
python3 create_manager_dataset.py
```

## Production Readiness Checklist

Run through this before any new asset goes to production:

### Datasets

- [ ] Dataset is scoped to a single audience
- [ ] Sensitive columns are removed from the dataset, not just hidden
- [ ] RLS is applied and tested with at least two user accounts
- [ ] Permissions assigned to groups, not individuals

### Chat Agents

- [ ] Agent connects to exactly one dataset
- [ ] Topic excludes columns that should not be queryable
- [ ] Agent refuses queries for individual records when the dataset has none
- [ ] Agent refuses queries for excluded columns

### Spaces and Knowledge Bases

- [ ] Every document classified before upload
- [ ] No document contains individual PII unless required for the audience
- [ ] View, query, and upload permissions set separately
- [ ] Upload restricted to content owners only

### Flows

- [ ] Flow uses an RLS-protected, column-pruned dataset
- [ ] Human approval step exists before any outbound action
- [ ] External credentials live in Secrets Manager, not in the Flow definition
- [ ] CloudTrail enabled and capturing Flow events

## Governance Framework

| Asset | Owner | Reviewer | Cadence |
|-------|-------|----------|---------|
| Datasets | Data steward | Security team | Quarterly |
| Dashboards | Analytics lead | Consumer group | Quarterly |
| Chat Agents | Agent builder | Data steward + Security | Monthly |
| Knowledge Bases | Content owner | Data steward | Monthly |
| Flows | Process owner | Security team | Per change |
| Spaces | Space admin | Data steward | Quarterly |

## Clean Up

To avoid ongoing charges, delete these resources when no longer needed:

- The Flow (Weekly Attrition Risk Alert)
- Three Chat Agents
- Knowledge Base documents
- The AnyCompany HR Space
- Three employee datasets + RLS rules dataset
- QuickSight groups
- CloudTrail trail (if created only for this walkthrough)

## License

This library is licensed under the MIT-0 License.
