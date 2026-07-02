# AWS Cost Saver

[![GitHub stars](https://img.shields.io/github/stars/prajapatimehul/claude-aws-cost-saver?style=social)](https://github.com/prajapatimehul/claude-aws-cost-saver/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/prajapatimehul/claude-aws-cost-saver/pulls)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Plugin-5C4EE5)](https://code.claude.com)
[![Plugin Version](https://img.shields.io/badge/version-2.1.0-blue.svg)](https://github.com/prajapatimehul/claude-aws-cost-saver)

Find what's wasting money in your AWS account.

```
You: "Scan my AWS for cost savings"
Claude: Found 8 issues. Potential savings: $340/month
```

## Installation

The plugin ships with both a `.claude-plugin/plugin.json` (Claude Code) and a `.codex-plugin/plugin.json` (Codex) manifest, so the same source tree installs cleanly into either agent.

### Claude Code

```
/plugin marketplace add prajapatimehul/claude-aws-cost-saver
/plugin install aws-cost-saver@aws-cost-saver-marketplace
```

Or interactively: `/plugin` → Marketplaces → Add Marketplace → `prajapatimehul/claude-aws-cost-saver`.

### Codex

```
codex plugin marketplace add prajapatimehul/claude-aws-cost-saver
```

Then launch Codex and run `/plugins` to browse and install **aws-cost-saver**.

### Other agents (MCP only)

Any agent that supports MCP can wire the AWS API server directly using [`plugins/aws-cost-saver/.mcp.json`](plugins/aws-cost-saver/.mcp.json) as a template. Skills live under [`plugins/aws-cost-saver/skills/`](plugins/aws-cost-saver/skills/) and can be loaded as plain Markdown.

## Quick Start

```bash
/aws-cost-saver:scan
```

Or just ask: `Scan my AWS account for cost savings`

**Requirements:** AWS credentials configured (`aws configure` or SSO), `uv` installed.

**This tool only reads data — it never modifies or deletes anything.** A PreToolUse hook enforces a read-only allowlist: only `describe-`/`get-`/`list-`/`lookup-`/`search-` style AWS CLI operations are permitted, and everything else is denied by default.

## Features

- **180 checks** across EC2, RDS, S3, Lambda, ECS, EKS, Aurora, SageMaker, and 30+ services
- **Parallel scanning** - 11 domain agents run simultaneously
- **Confidence scoring** - filters false positives
- **Real pricing** - uses AWS Cost Explorer
- **Markdown reports** - clean, actionable output

## What's included

### Commands (slash)

| Command | Description |
|---------|-------------|
| `/aws-cost-saver:scan` | Full cost optimization scan across 11 domains |

### Skills (auto-loaded)

| Skill | Description |
|-------|-------------|
| `reviewing-findings` | Confidence-scored review; filters false positives |
| `validating-aws-pricing` | Mandatory Pricing-API/verified-table sanity check before reporting |

### Subagent

`aws-cost-saver:aws-cost-saver` — domain-scoped scanner. Invoke 11 in parallel (one per domain) for a full account audit.

### MCP server

The plugin pre-wires the **AWS API MCP server** in [`.mcp.json`](plugins/aws-cost-saver/.mcp.json), launched via `uvx` from `scripts/start-mcp.sh`. A PreToolUse hook in [`hooks/hooks.json`](plugins/aws-cost-saver/hooks/hooks.json) (backed by [`scripts/guard_readonly.py`](plugins/aws-cost-saver/scripts/guard_readonly.py)) denies every non-read AWS CLI operation, keeping every scan read-only by default.

### CLI

The repository also ships a standalone Python CLI (`main.py`) usable without a coding agent:

```bash
python main.py checks                       # list all 180 checks
python main.py check EC2-001                # detail for one check
python main.py scan-info                    # show the AWS CLI commands a scan runs
python main.py spend-hotspots --profile X   # rank scan order by real spend (Cost Explorer)
python main.py report --findings findings.json
```

---

## Demo

### Results

![Scan results - findings and savings](public/final.png)

### Before & After

![60% cost reduction - $105/day to $42/day](public/cost-savings-60-percent.png)
*Real AWS account: $105/day → $42/day after running the scanner*

### How it works

**Step 1:** Choose compliance requirements

![Scan step 1 - Compliance selection](public/scan-step-1.png)

**Step 2:** Parallel agents scan your account

![Scan step 2 - Parallel domain scanning](public/step-2-process.png)

---

## Domains & Checks

| Domain | Checks | Key Areas |
|--------|--------|-----------|
| **Compute** | 28 | EC2 idle/over-provisioned, Compute Optimizer ML, Cost Optimization Hub, EBS, GP2→GP3 |
| **Storage** | 24 | S3 lifecycle, CloudWatch Logs, snapshots, Secrets Manager |
| **Database** | 17 | RDS idle/over-provisioned, extended support, Valkey migration, RI coverage |
| **Networking** | 19 | Unused EIPs, NAT, public IPv4, data transfer, VPC endpoints |
| **Serverless** | 11 | Lambda memory, unused functions, SQS provisioned-mode idle |
| **Reservations** | 13 | RI/SP/Database SP coverage, purchase recommendations |
| **Containers** | 17 | ECS/EKS idle, extended support versions, Fargate, Spot, ECR lifecycle |
| **Advanced DBs** | 18 | Aurora, DocumentDB, Neptune, Redshift |
| **Analytics** | 15 | SageMaker, EMR, OpenSearch |
| **Data Pipelines** | 12 | Kinesis, MSK, Glue |
| **Storage Advanced** | 6 | FSx, AWS Backup |

## Troubleshooting

**"MCP server not found"** — Install `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**"AWS credentials not configured"** — Run `aws configure` or `aws sso login`

**"Access Denied"** — Use `ReadOnlyAccess` policy or check permissions

## License

MIT
