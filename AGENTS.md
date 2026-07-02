# AWS Cost Saver for Codex

## Purpose

This repository ships plugin manifests for both **Claude Code**
(`.claude-plugin/`) and **Codex** (`.codex-plugin/`, `.agents/plugins/`).

If your Codex version supports plugin marketplaces, install the plugin directly:

```bash
codex plugin marketplace add prajapatimehul/claude-aws-cost-saver
```

Then run `/plugins` inside Codex to install **aws-cost-saver**.

Otherwise, use the MCP-only setup below: the runtime entrypoint is the AWS MCP
server plus these repository instructions, not `/aws-cost-saver:scan`.

## Codex Setup (MCP only)

Run these commands from the repository root:

```bash
# Register the AWS API MCP server used by this project
codex mcp add awslabs-aws-api --env FASTMCP_LOG_LEVEL=ERROR -- ./plugins/aws-cost-saver/scripts/start-mcp.sh

# Verify it is available
codex mcp list
```

Requirements:
- AWS credentials already configured (`aws configure` or `aws sso login`)
- `uv` installed

## Codex Operating Rules

- Use `plugins/aws-cost-saver/checks/all_checks.yaml` as the baseline catalog of checks.
- Always query AWS Cost Explorer first for actual spend before estimating savings.
- Treat Cost Optimization Hub and Compute Optimizer as optional accelerators, not prerequisites.
- Do not fail the scan if optional recommendation sources are disabled or empty.
- Output Markdown only.
- Save merged scan output to `findings.json` when asked to produce reportable results.

## Mandatory Pricing Rules

- Never guess `monthly_savings`.
- Pricing resolution order:
  1. AWS Pricing API
  2. Exact verified table entry
  3. `monthly_savings = 0` with `pricing_unknown = true`
- Savings must not exceed actual service spend from Cost Explorer.

## Suggested Codex Prompt

```text
Scan my AWS account for cost savings using AWS MCP.
Use plugins/aws-cost-saver/checks/all_checks.yaml as the baseline.
Get actual monthly spend from Cost Explorer first.
Treat Cost Optimization Hub and Compute Optimizer as optional.
Return Markdown only and save merged findings to findings.json.
```

## Reference Files

- Scanner instructions: `plugins/aws-cost-saver/agents/aws-cost-saver.md`
- Claude Code command flow: `plugins/aws-cost-saver/commands/scan.md`
- Pricing validation skill: `plugins/aws-cost-saver/skills/validating-aws-pricing/SKILL.md`
- Findings review skill: `plugins/aws-cost-saver/skills/reviewing-findings/SKILL.md`
