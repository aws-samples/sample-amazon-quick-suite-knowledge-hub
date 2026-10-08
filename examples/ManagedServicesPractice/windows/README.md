# MSP Windows Operations — Powered by Amazon Quick Desktop

AI-driven Windows workspace management for Managed Services Practice (MSP) teams — provisioning, software installs, RDP validation, and patch compliance run from a single app by **two Quick Desktop agents**.

## The Problem

An MSP ops engineer's day is spread across four or five tools: the AWS console for provisioning, an RDP client for access, a ticketing system (Jira) for the work queue, and a separate patch dashboard for compliance. Workspaces are built from gold images that drift out of spec, patching is a manual fire drill, and software gets rolled out in bulk whether or not anyone needs it. The result is slow onboarding, compliance gaps, and a lot of tool-switching toil.

## The Solution

Amazon Quick Desktop runs an AI work companion natively on the operator's machine — reaching into local files, the terminal, and connected tools (Jira, S3, custom MCP servers) while tapping the same Quick spaces and content used on the web. The entire MSP day — **ticket → provision → install → validate (RDP) → close** — happens in one pane of glass.

The solution is built on **two Quick Desktop agents** that hand off to each other through shared local state:

| Agent | Role | Definition |
|-------|------|------------|
| **GSI-Onboarding-With-RDP** | IT Operations Analyst — fulfills Jira MSP tickets by provisioning Windows workspaces, installing software, and opening RDP sessions. | [`GSI-Onboarding-With-RDP.md`](./GSI-Onboarding-With-RDP.md) |
| **GSI-MSP-Compliance** | Scheduled compliance & patching agent — scans the managed Windows fleet for missing patches and remediates them, with a visual dashboard. | [`GSI-MSP-Compliance.md`](./GSI-MSP-Compliance.md) |

Both are **persistent agent personas** in Quick Desktop — each with its own identity, instructions, connected tools, and domain knowledge. The onboarding agent is chatted with directly to work tickets; the compliance agent runs on a schedule (every morning at 10 AM CST) or on demand.

## How the Two Agents Fit Together

The agents are deliberately separate personas, but they operate on the **same fleet** and coordinate through shared files on the operator's local machine. The onboarding agent builds and validates a workspace; the compliance agent keeps it patched over its lifetime.

```
                          ┌─────────────────────────────────────────────┐
                          │              Quick Desktop                   │
                          │   (local files · terminal · MCP · Jira · S3) │
                          └─────────────────────────────────────────────┘
                                   │                          │
          ┌────────────────────────┘                          └────────────────────────┐
          ▼                                                                             ▼
┌───────────────────────────────┐                               ┌───────────────────────────────┐
│  GSI-Onboarding-With-RDP       │                               │  GSI-MSP-Compliance            │
│  (ticket-driven, interactive)  │                               │  (scheduled / on-demand)       │
│                                │                               │                                │
│  Ticket 1: Provision           │  writes instance ID           │  Step 0: adopt active instance │
│   provision_windows_ec2  ──────┼──────────────────────────────▶│   into managed_instances.txt   │
│   (installs Python + Git)      │   active_instance.txt         │                                │
│                                │                               │  Step 1: check_compliance      │
│  RDP checkpoint (validate)     │                               │   → HTML dashboard             │
│   open_rdp                     │                               │                                │
│                                │                               │  Step 3: patch_fleet (Scan)    │
│  Ticket 2: Install tooling     │                               │   then patch_fleet (Install,   │
│   install_software             │                               │   RebootIfNeeded) on approval  │
│   (Notepad++, VS Code)         │                               │                                │
│                                │                               │  re-render dashboard           │
│  Close tickets in Jira         │                               │                                │
└───────────────────────────────┘                               └───────────────────────────────┘
          │                                                                             ▲
          └──────────────── provisioned, SSM-registered, RDP-ready instance ───────────┘
```

**The handoff — shared state files on the operator's machine:**

| File | Written by | Read by | Purpose |
|------|-----------|---------|---------|
| `active_instance.txt` | Onboarding (after both tickets Done) | Onboarding (Ticket 2, RDP), Compliance (Step 0) | The current freshly-provisioned, fully-ready instance. |
| `managed_instances.txt` | Compliance (Step 0, appends active instance) | Compliance (Steps 1 & 3) | The full fleet of instances under patch management. |
| `client_profiles_windows.md` | Operator (standards source of truth) | Onboarding (provisioning) | Per-client config: subnet, security group, instance type, naming, tags. |

This is where **standards get enforced automatically**: the onboarding agent reads the client profile instead of cloning a gold image, so every workspace is built to spec. The Activity Feed and Knowledge Graph surface the right tickets and context so the operator never has to hunt across Slack, email, and Jira.

## End-to-End Walkthrough (Run-of-Show)

One app, one operator: **ticket → provision → install → validate → close.**

### Part 1 — GSI-Onboarding-With-RDP (provision a NovaTech workspace)

1. **Start with the ticket.** Open the Jira MSP ticket from the Activity Feed ("Work on this").
2. **Provision.** The agent reads `client_profiles_windows.md` and calls `provision_windows_ec2` — Windows Server 2022, SSM-ready, Fleet Manager RDP, Administrator pre-configured. Python + Git install silently in the background. Client profiles mean the workspace is built to spec, not from a drifting gold image. Ticket transitions To Do → In Progress → Done.
3. **Validate with RDP.** At the mandatory checkpoint, the agent offers to `open_rdp` so the operator can see the live workspace before moving on.
4. **Next ticket — install tooling.** The agent reads `active_instance.txt` and calls `install_software` for Notepad++ and VS Code.
5. **Validate again** with RDP if desired.
6. **Close the ticket** back in Jira.

### Part 2 — GSI-MSP-Compliance (patch the fleet)

1. **The scheduled run.** The recurring question every MSP lives with: are the client's servers patched? This agent runs on a schedule (10 AM CST) or on demand.
2. **Run compliance.** It adopts the active instance into `managed_instances.txt`, runs `check_compliance` across the fleet, and renders a dark-themed HTML dashboard with per-server patch status.
3. **Remediate.** On approval, `patch_fleet` scans and then installs missing patches (`RebootIfNeeded`), then re-renders the dashboard.
4. **Close out.** Report patches applied and current compliance.

**Three takeaways:** single pane of glass (no tool-switching), standards enforced automatically (client profiles over gold images), and trust through validation (live RDP + a real compliance dashboard, not blind checkmarks).

## What's in This Folder

| File | Description |
|------|-------------|
| `GSI-Onboarding-With-RDP.md` | Agent definition for the onboarding/RDP persona — ticket workflow, mandatory RDP checkpoint, provisioning + install steps, Jira transitions, presentation rules. |
| `GSI-MSP-Compliance.md` | Agent definition for the scheduled compliance/patching persona — fleet adoption, dashboard rendering, scan/install workflow. |
| `mcp_provision_windows_ec2.py` | MCP server for provisioning a Windows Server 2022 EC2 instance (SSM access, Fleet Manager RDP, Administrator pre-set, Chocolatey + Python/Git via UserData). |
| `mcp_install_windows_software.py` | MCP server for installing software on a Windows instance via Chocolatey through SSM Run Command (async). |
| `mcp_patch_windows.py` | MCP server for fleet patch compliance and remediation via AWS Systems Manager. |

Each `.py` file is an AWS Lambda function implementing the Model Context Protocol (MCP). Quick Desktop calls them via MCP Action connectors backed by API Gateway endpoints.

## MCP Tools Reference

### `mcp_provision_windows_ec2.py`

| Tool | Description |
|------|-------------|
| `provision_windows_ec2` | Launches a Windows Server 2022 instance (latest AMI), sets the Administrator password, enables Fleet Manager RDP (no key pair, no port 3389), installs Chocolatey + Python/Git via UserData, and tags for SOC2/`Patch Group`. Required: `instance_name`, `client`, `subnet_id`, `security_group_ids`. Optional: `instance_type` (`t3.large`), `volume_size` (100 GB), `instance_profile` (`EC2-SSM-Role`), `environment`, `admin_password`. |

### `mcp_install_windows_software.py`

| Tool | Description |
|------|-------------|
| `install_software` | Installs Chocolatey packages (e.g. `notepadplusplus`, `vscode`, `python3`, `git`) on a Windows instance via SSM Run Command. Returns a command ID immediately; install runs asynchronously. Required: `instance_id`, `packages`. |

### `mcp_patch_windows.py`

| Tool | Description |
|------|-------------|
| `patch_fleet` | Applies Windows OS patches via SSM. `Scan` is auto-approved; `Install` requires explicit operator approval. Required: `instance_ids`, `operation` (`Scan`/`Install`). Optional: `reboot_option` (`RebootIfNeeded`/`NoReboot`). |
| `check_patch_status` | Checks the status of a prior patch operation by `command_id`. |
| `check_compliance` | Returns installed / missing / failed patch counts per instance. Required: `instance_ids`. |

## Deployment

1. Deploy the three `.py` files as separate AWS Lambda functions (Python 3.12).
2. Create API Gateway REST endpoints for each Lambda.
3. In Amazon Quick Desktop, register the API Gateway URLs as MCP Action connectors.
4. Connect the Jira MSP project (via the Jira 2LO connector) to the onboarding agent.
5. Create the two agents in Quick Desktop using the instructions in `GSI-Onboarding-With-RDP.md` and `GSI-MSP-Compliance.md`.
6. Place the shared state files on the operator's machine: `client_profiles_windows.md`, `active_instance.txt`, `managed_instances.txt` (default location `C:\PartnerSA\infosys\MSP\`).
7. Schedule the compliance agent (e.g. daily at 10 AM CST).

## Prerequisites

- AWS account with Amazon Quick (Desktop) enabled
- Windows Server 2022 support with SSM Agent and an IAM instance profile for SSM (`EC2-SSM-Role` or equivalent)
- A subnet and security group for instance placement (Fleet Manager RDP needs no inbound 3389)
- Jira MSP project reachable via the Quick Jira connector
- IAM roles with Lambda execution, EC2, and SSM permissions

## AWS Services

Amazon Quick (Desktop, Agents, Activity Feed, Knowledge Graph) · Amazon EC2 (Windows Server 2022) · AWS Systems Manager (SSM Run Command, Patch Manager, Fleet Manager RDP) · AWS Lambda · API Gateway · Jira (via connector) · MCP Actions

## Business Outcomes

| Use Case | Outcome |
|----------|---------|
| **Single pane of glass** | Ticket → provision → install → validate → close, with no tool-switching between console, RDP, ticketing, and dashboards. |
| **Standards enforced automatically** | Client profiles replace gold images — every workspace is built to spec and compliant. |
| **Trust through validation** | Live RDP sessions and a real compliance dashboard, not blind checkmarks. |
| **Autonomous compliance** | Scheduled, hands-off patch scanning and remediation with an approval gate — not a manual fire drill. |
