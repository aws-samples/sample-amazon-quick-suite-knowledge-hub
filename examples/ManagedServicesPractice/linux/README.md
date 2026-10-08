# MSP EC2 Operations — Linux

AI-driven EC2 lifecycle management for Managed Services Practice (MSP) teams, powered by Amazon Quick.

## The Problem

MSP teams manage multiple customers across complex IT environments. Traditional operations rely on gold images that are expensive to maintain, manual patch cycles that take days, and mass software rollouts that waste licenses. The result: slow provisioning, compliance gaps, and reactive support.

## The Solution

Amazon Quick replaces manual MSP operations with AI-driven automation — no gold images required. The configuration lives in a **Quick Space** and the work is driven by a **Chat agent** plus a **JIT Flow**:

- **Space** — holds the client profiles and the approved-software list. This is the source of truth the agent assembles configurations from at runtime.
- **Chat agent** — created with the Space and all MCP Actions attached. For example:

  > *"You are the EC2 Dynamic Provisioning Agent. You replace the traditional Gold Image process by assembling client-specific configurations at runtime from the linked Space."*

  The same agent handles patching and compliance.
- **JIT Flow** — installs required software (only if approved), triggered by email.

The solution covers three workflows:

```
Provision → JIT Software Install → Patch / Compliance
```

1. **Provision** — Run from Quick **Desktop**. The agent reads the client profile from the Space and calls the provisioning MCP connector to launch a fully configured EC2 instance. A JIT shell hook is deployed automatically via User Data at first boot.
2. **JIT Software Install** — Triggered when someone logs into the EC2 instance and types a command that isn't installed (e.g. `docker`). The shell hook fires, an email kicks off the JIT Flow, and within a few minutes the software is installed and the command works — provided it's on the approved list in the Space.
3. **Patch / Compliance** — Handled by the Chat agent: it scans fleet compliance, presents a patch spec with pre-patch snapshots (SOC2), and waits for operator approval before executing.

## What's in This Folder

| File | Description |
|------|-------------|
| `msp_provision_ec2.py` | MCP server for EC2 provisioning — launches an instance with standard MSP tagging (Client, Environment, CostCenter, PatchGroup), encrypted gp3 root volume, IMDSv2 required, and a JIT `command_not_found` shell hook deployed via User Data at first boot. |
| `mcp_patch_ec2.py` | MCP server for fleet patching — scans compliance, takes pre-patch EBS snapshots, runs `AWS-RunPatchBaseline`, and installs standard packages (SSM Agent, CloudWatch Agent). Targets instances by `PatchGroup` tag. |
| `jit_install_software.py` | MCP server for just-in-time software deployment — installs packages on EC2 instances via SSM Run Command using `dnf`, `yum`, or `pip3`. |

All three are AWS Lambda functions that implement the Model Context Protocol (MCP). Quick calls them via MCP Action connectors backed by API Gateway endpoints.

## How the Pieces Fit Together

### 1. Provisioning (Quick Desktop → `msp_provision_ec2.py`)

From Quick Desktop, the operator asks the Dynamic Provisioning Agent: *"Create a new NovaTech server"*. The agent reads the client config from the linked Space and calls the provisioning MCP connector, which launches the instance and returns the instance ID. The JIT shell hook is baked in via User Data, so it's active on first login.

**Tool exposed by `msp_provision_ec2.py`:**

| Tool | Description |
|------|-------------|
| `provision_ec2_instance` | Launches an EC2 instance with MSP standard config: tags (Client, Environment, CostCenter, Owner, PatchGroup, BackupPlan), encrypted gp3 root volume, IMDSv2 required, IAM instance profile for SSM, and a JIT `command_not_found` hook via User Data. Required: `client`, `ami_id`, `instance_type`, `subnet_id`, `security_groups`, `key_name`. Optional: `instance_profile` (`EC2-SSM-Role`), `environment`, `cost_center`, `owner`, `instance_name`, `root_volume_size` (100 GB), `patch_group`. |

### 2. JIT Software Install (Shell Hook → JIT Flow → `jit_install_software.py`)

When a user SSHs into the instance and runs a command that isn't installed (e.g. `docker`), the shell hook fires:

```
docker is not installed on this instance.
To request installation, please send an email to:
  jit_install_flow@us-east-1.mail.quick.aws.com
  Subject: INSTALL:docker:<instance_id>:NovaTech
```

The email triggers the **JIT Flow**, which checks the request against the approved-software list in the Space and, if approved, calls the JIT install MCP connector. Within a few minutes the package is installed and the command works.

**Tool exposed by `jit_install_software.py`:**

| Tool | Description |
|------|-------------|
| `deploy_software` | Installs a package on an EC2 instance via SSM Run Command. Supports `dnf`, `yum`, and `pip3` install methods. |

### 3. Patch / Compliance (Chat agent → `mcp_patch_ec2.py`)

The operator asks the Chat agent: *"Check patch status for NovaTech production instances"*.

**Tools exposed by `mcp_patch_ec2.py`:**

| Tool | Description |
|------|-------------|
| `patch_fleet` | Executes patching on EC2 instances by PatchGroup tag. Takes pre-patch EBS snapshots, runs `AWS-RunPatchBaseline`, and installs standard packages. Blast radius capped at 20 instances per batch. |
| `check_patch_status` | Returns per-instance compliance results — installed, missing, and failed patch counts. Can check a specific command run or overall compliance. |

The patching flow includes an **approval gate** — the agent presents a patch spec (snapshot plan, reboot policy) and waits for the operator to type "yes" before executing. This enforces SOC2/HIPAA compliance.

## Deployment

1. Deploy all three `.py` files as separate AWS Lambda functions (Python 3.12)
2. Create API Gateway REST endpoints for each Lambda
3. In Amazon Quick, register the API Gateway URLs as MCP Action connectors
4. Create a Quick Space holding the client profiles and the approved-software list
5. Create the Chat agent with the Space and all MCP Actions attached
6. Build the JIT Flow to receive install-request emails and call the JIT install connector
7. Tag target EC2 instances with a `PatchGroup` tag (e.g. `NovaTech-Prod-Linux`)

## Business Outcomes

| Use Case | Outcome |
|----------|---------|
| **Dynamic Provisioning** | Eliminates gold image maintenance — configurations are assembled at runtime from the Space |
| **Autonomous Compliance** | 100% patch visibility with pre-patch snapshots and approval gates |
| **Just-in-Time Software** | Zero mass rollouts — approved apps install on-demand, cutting license waste |

## Prerequisites

- AWS account with Amazon Quick enabled
- EC2 instances with SSM Agent installed and an IAM instance profile for SSM
- A Quick Space with client profiles and an approved-software list
- IAM roles with Lambda execution, SSM, and EC2 permissions

## AWS Services

Amazon Quick (Chat, Desktop, Spaces, Flows) · Amazon EC2 · AWS Systems Manager (SSM) · AWS Lambda · API Gateway · MCP Actions
