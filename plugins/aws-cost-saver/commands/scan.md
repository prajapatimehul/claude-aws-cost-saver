---
description: Scan AWS account for cost optimization
allowed-tools: Read, Task, Write, Bash, Glob, Grep, AskUserQuestion, mcp__awslabs-aws-api__call_aws
---

# AWS Cost Optimization Scan

## Step 0: Ask for AWS Profile

First, ask the user which AWS profile to use:

```json
{
  "questions": [{
    "header": "AWS Profile",
    "question": "Which AWS profile should I use for the scan?",
    "multiSelect": false,
    "options": [
      {"label": "default", "description": "Use default AWS credentials"},
      {"label": "Enter profile name", "description": "I'll type my profile name"}
    ]
  }]
}
```

If user selects "Enter profile name" or "Other", ask them to type the profile name.

Store the profile name for all subsequent AWS commands.

**Authentication:** AWS commands work with any valid AWS credentials:
- SSO profile: `--profile your-sso-profile`
- Named profile: `--profile your-profile`
- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- IAM role (EC2/Lambda): automatic

## Step 1: Discover Account

```bash
aws sts get-caller-identity --profile {profile}
```

Extract:
- **Account ID** → use in report header
- **Profile name** → use for file naming: `reports/findings_{profile}.json`

## Step 2: Find Active Regions

```bash
# Get all regions
aws ec2 describe-regions --profile {profile} --query 'Regions[].RegionName' --output json

# Check which have EC2 instances (quick check)
aws ec2 describe-instances --profile {profile} --query 'Reservations[].Instances[0].Placement.AvailabilityZone' --output text
```

Only scan regions with resources.

## Step 3: Ask Compliance

Use `AskUserQuestion`:

```json
{
  "questions": [{
    "header": "Compliance",
    "question": "Which compliance requirements apply?",
    "multiSelect": true,
    "options": [
      {"label": "HIPAA", "description": "Healthcare - skip PHI resources"},
      {"label": "SOC2", "description": "Audit - preserve all logs"},
      {"label": "PCI-DSS", "description": "Payments - skip payment infra"},
      {"label": "None", "description": "No compliance requirements"}
    ]
  }]
}
```

## Step 3.5: Check Compute Optimizer Enrollment

```bash
aws compute-optimizer get-enrollment-status --profile {profile}
```

If status is `Inactive`, inform the user:
> "AWS Compute Optimizer is not enabled. It provides free ML-powered rightsizing and idle detection. Enable with: `aws compute-optimizer update-enrollment-status --status Active`"

Store `compute_optimizer_active: true/false` in metadata. Pass to domain agents.

## Step 4: Get Actual Monthly Spend + Data Transfer Breakdown

**CRITICAL:** Query AWS Cost Explorer for real billing data.

```bash
# Service-level spend
aws ce get-cost-and-usage \
  --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

**NEW: Also query USAGE_TYPE for data transfer costs (same API, $0.01 extra):**

```bash
# Data transfer breakdown (surfaces hidden costs)
aws ce get-cost-and-usage \
  --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=USAGE_TYPE \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute"]}}'
```

Store both `actual_monthly_spend` and `data_transfer_breakdown` in metadata.

**Note:** If no `--profile` is provided, AWS CLI uses default credentials (env vars or IAM role).

## Step 4.5: Build a Spend-Led Scan Order

Do not treat all domains equally. Rank them from actual spend first.

```bash
python main.py spend-hotspots --profile {profile} --output reports/spend_hotspots_{profile}.md
```

If you need more EC2 detail and the account supports it, optionally query resource-level spend:

```bash
aws ce get-cost-and-usage-with-resources \
  --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute"]}}'
```

Optional accelerators:
- Cost Optimization Hub: use recommendations if available, but do not fail the scan if the service is disabled or returns nothing.
- Public IP Insights: if IPAM is enabled, use it to inventory billable public IPv4 addresses before relying only on ENI or EIP inventory.

## Step 5: Parallel Domain Scan

Launch 11 `aws-cost-saver:aws-cost-saver` subagents **in parallel**:

| Agent | Domain | Checks |
|-------|--------|--------|
| 1 | compute | EC2, EBS, AMIs, snapshots, EIPs, Compute Optimizer |
| 2 | storage | S3, EFS, CloudWatch Logs, CloudTrail, Secrets Manager |
| 3 | database | RDS, DynamoDB, ElastiCache |
| 4 | networking | NAT, ELB, VPC endpoints, public IPv4, data transfer, Route 53 |
| 5 | serverless | Lambda, API Gateway, SQS, Step Functions |
| 6 | reservations | RI coverage, Savings Plans, purchase recommendations |
| 7 | containers | ECS, EKS, Fargate, ECR |
| 8 | advanced_databases | Aurora, DocumentDB, Neptune, Redshift |
| 9 | analytics | SageMaker, EMR, OpenSearch, QuickSight |
| 10 | data_pipelines | Kinesis, MSK, Glue, EventBridge |
| 11 | storage_advanced | FSx, AWS Backup |

Pass to each:
- `region`: Active region(s)
- `compliance`: User's selection
- `profile`: AWS profile name
- `compute_optimizer_active`: Whether CO is enrolled (for compute domain)
- `data_transfer_breakdown`: USAGE_TYPE costs (for networking domain)
- `spend_hotspots`: Top services and usage types from Step 4.5

## Step 6: Merge, Anti-Hallucination Check & Quick Review

Combine all 11 outputs into `reports/findings_{profile}.json`.

### 6.0: Anti-Hallucination Verification (MANDATORY)

The subagent SHOULD apply these rules internally. This step is a **safety net** to catch any violations that slipped through. Verify EVERY finding:

**Check 1: pricing_source exists**
Every finding MUST have `details.pricing_source` set to one of:
- `aws_pricing_api` — price from Pricing API
- `verified_table` — price from hardcoded table (us-east-1 only)
- `aws_cost_explorer` — price from Cost Explorer (RI/SP)
- `pricing_unknown` — price not found, monthly_savings MUST be 0

If `pricing_source` is missing → **REJECT**, set monthly_savings=0, add pricing_unknown=true.

**Check 2: calculation exists**
Every finding with monthly_savings > 0 MUST have `details.calculation` with formula and numbers. If missing → **REJECT**.

**Check 3: No fabricated prices**
Any finding without `pricing_source` but with `monthly_savings > 0` → set to 0 with `pricing_unknown: true`.

**Check 4: No missing-data-as-zero**
Idle findings where any signal has `"status": "no_data"` but was counted as meeting threshold → recalculate idle score without that signal.

**Check 5: Downsize targets are valid**
Over-provisioned findings must use Compute Optimizer target OR one size down in same family. Otherwise → set monthly_savings=0.

**Check 6: RI/SP and Spot coverage noted**
Any EC2/RDS finding should have `covered_by_ri_or_sp` or `instance_lifecycle` in details if applicable. If missing for high-savings findings → flag for manual review.

**Check 7: IaC-managed resources flagged**
Findings recommending deletion of resources with `aws:cloudformation:stack-name` or terraform tags should have `managed_by_iac: true` and adjusted recommendation.

### Quick Review (inline - no scripts):

Apply 2 adjustments only:
1. **Resource Age:** -30% confidence if created < 7 days ago
2. **Environment:** -10% for production, +10% for dev/test (from Name tags)

Mark findings:
- `approved` if confidence ≥ 70%
- `needs_validation` if confidence 50-69%
- `filtered` if confidence < 50%

## Step 6.5: MANDATORY Price Validation

**CRITICAL:** Before generating the report, validate ALL pricing calculations.

### 6.5.1: Get Service-Level Billing

Query Cost Explorer for service breakdown:
```bash
aws ce get-cost-and-usage --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

### 6.5.2: Sanity Check Each Finding

For EVERY finding, verify:
```
finding.monthly_savings <= service_monthly_spend
```

**Example Check:**
- Finding: CloudWatch Logs savings = $594/mo
- Billing: CloudWatch total = $159/mo
- **FAIL** - Finding exceeds service spend!

### 6.5.3: Verify Calculation Formula

Each finding type has a SPECIFIC formula:

| Finding Type | Correct Formula |
|--------------|-----------------|
| CW Logs Retention | `stored_gb × $0.03` (storage only) |
| Unattached EBS | `size_gb × price_per_gb` |
| Idle EC2 | `hourly_rate × 730` |
| Over-provisioned | `current_cost - recommended_cost` |
| No RI Coverage | `on_demand_cost × savings_percent` |
| Public IPv4 Charges | `address_count × $0.005 × 730` |
| Idle EKS Cluster | `(cluster_hourly_rate × 730) + node_costs` |
| Idle Fargate Task | `vCPU_hrs × $0.04048 + GB_hrs × $0.004445` |
| Idle Aurora | `hourly_rate × 730 + storage_gb × $0.10` |
| Idle Redshift | `node_hourly × nodes × 730` |
| Idle SageMaker Endpoint | `hourly_rate × 730` |
| Over-provisioned Kinesis | `shards × $0.015/hr × 730` |
| Idle MSK Cluster | `broker_cost × brokers` |
| NAT Data Processing | `gb_processed × $0.045` |
| Cross-AZ Transfer | `gb_transferred × $0.02` (both directions) |
| Unused Secrets | `count × $0.40` |
| Unused Route 53 Zone | `count × $0.50` |
| EBS Snapshot Archive | `gb_months × ($0.05 - $0.0125)` |
| RI Purchase Savings | From `ce get-reservation-purchase-recommendation` |
| SP Purchase Savings | From `ce get-savings-plans-purchase-recommendation` |

### 6.5.4: Flag & Correct Invalid Findings

If a finding fails validation:
1. Recalculate using correct formula
2. Update `monthly_savings` with corrected value
3. Add `pricing_corrected: true` to details
4. Document the correction reason

### 6.5.5: Get Detailed Cost Breakdown (for >$100 findings)

For findings claiming >$100 savings, query usage type breakdown:
```bash
aws ce get-cost-and-usage --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Dimensions": {"Key": "SERVICE", "Values": ["AmazonCloudWatch"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE
```

This reveals actual storage vs ingestion costs.

## Step 6.6: Calculate Priority Scores

Rank findings by actionability, not just savings amount.

### Formula

```
priority_score = impact × confidence × urgency × risk_multiplier
```

### Components

| Component | Calculation | Range |
|-----------|-------------|-------|
| Impact | min(monthly_savings / 100, 10) | 0.1 - 10 |
| Confidence | confidence / 100 | 0.5 - 1.0 |
| Urgency | Based on idle duration | 0.7 - 1.5 |
| Risk Multiplier | Based on environment | 0.5 - 1.5 |

### Urgency Values

| Idle Duration | Urgency |
|---------------|---------|
| > 30 days | 1.5 |
| 14-30 days | 1.2 |
| 7-14 days | 1.0 |
| < 7 days | 0.7 |

### Risk Multiplier Values

| Environment | Multiplier |
|-------------|------------|
| dev/test | 1.5 (lower risk) |
| staging | 1.0 |
| production | 0.5 (higher risk) |

### Example

```
Finding: Idle RDS ($365/mo), 85% confidence, 21 days idle, production

impact = min(365/100, 10) = 3.65
confidence = 0.85
urgency = 1.2 (14-30 days)
risk = 0.5 (production)

priority_score = 3.65 × 0.85 × 1.2 × 0.5 = 1.86 (MEDIUM)
```

### Priority Ranking

| Score | Priority | Action |
|-------|----------|--------|
| > 5.0 | Critical | This week |
| 2.0-5.0 | High | This month |
| 1.0-2.0 | Medium | Next quarter |
| < 1.0 | Low | When convenient |

## Step 7: Generate Report

Create `reports/cost_report_{profile}.md`:

```markdown
# AWS Cost Optimization Report

**Account:** {account_id}
**Profile:** {profile}
**Date:** {scan_date}
**Compliance:** {compliance}

## Summary

| Metric | Value |
|--------|-------|
| Actual Monthly Spend | $X,XXX |
| Approved Savings | $XXX/mo |
| Needs Validation | $XX/mo |

## Quick Wins (Implement This Week)
...

## Top Savings Opportunities
...

## Needs Validation
...
```

Show user:
1. Total approved savings
2. Top 5 findings
3. Ask which to implement
