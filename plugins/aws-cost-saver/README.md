# aws-cost-saver

180 AWS cost-optimization checks across 11 domains, bundled as a plugin for **Claude Code** and **Codex**. Read-only by design — every finding is priced from the AWS Pricing API or a verified table, never guessed.

## Install

### Claude Code

```
/plugin marketplace add prajapatimehul/claude-aws-cost-saver
/plugin install aws-cost-saver@aws-cost-saver-marketplace
```

### Codex

```
codex plugin marketplace add prajapatimehul/claude-aws-cost-saver
```

Then launch Codex and run `/plugins` to browse and install **aws-cost-saver**.

## What's included

### MCP server

This plugin configures the [AWS API MCP server](https://github.com/awslabs/mcp/tree/main/src/aws-api-mcp-server) (`mcp__awslabs-aws-api__call_aws`) via `.mcp.json`. A PreToolUse hook (`hooks.json`) blocks any write/mutation verbs so the scan stays read-only.

### Commands

| Command | Description |
|---------|-------------|
| `/aws-cost-saver:scan` | Full parallel scan across 11 domains, with Cost Explorer + Compute Optimizer + RI/SP recommendations. |

### Skills

| Skill | Description |
|-------|-------------|
| `reviewing-findings` | Confidence-scored review of findings to filter false positives. |
| `validating-aws-pricing` | Mandatory price validation against Pricing API / verified table before generating any report. |

### Agent

`aws-cost-saver` — a domain-scoped subagent. Invoke 11 in parallel (one per domain) to scan an account.

## CLI

The plugin ships alongside a Python CLI (`main.py` at the repo root) for offline use:

```bash
python main.py checks                       # list all 180 checks
python main.py check EC2-001                # detail for one check
python main.py spend-hotspots --profile X   # rank scan order by actual spend
python main.py report --findings findings.json
```

See the [repository README](../../README.md) for full CLI reference.
