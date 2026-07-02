# AWS Cost Optimization System

## Overview

174 automated cost optimization checks across 11 domains.
Integrates **AWS Compute Optimizer** (free ML-powered rightsizing/idle detection),
**Data Transfer cost analysis** (USAGE_TYPE breakdown), **Public IPv4 charge detection**, and **Reservation Purchase Recommendations**.
Designed for use with **Claude Code + AWS MCP** for direct account scanning.
Output: **Markdown only** (no Excel/PDF/HTML).

## Quick Start

```bash
# List all checks
python main.py checks

# View specific check
python main.py check EC2-001

# See required AWS CLI commands
python main.py scan-info

# Rank the scan by actual spend before scanning all domains
python main.py spend-hotspots --profile your-profile
```

## How to Scan an AWS Account

Claude Code uses the AWS MCP tool (`mcp__awslabs-aws-api__call_aws`) to scan.

### Step 1: Discover Regions
```
aws ec2 describe-regions --query 'Regions[].RegionName'
```

### Step 2: Get Actual Monthly Spend (CRITICAL)

**Always query AWS Cost Explorer first** to get real billing data:

```bash
aws ce get-cost-and-usage \
  --time-period Start=2025-12-01,End=2026-01-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

This returns actual costs including:
- Data transfer charges
- API request costs
- Marketplace subscriptions
- Taxes and support fees

**Do NOT estimate total spend from resource counts** - it will be wrong.

### Step 2.5: Build a Spend-Led Scan Order

Rank the scan from Cost Explorer first:

```bash
python main.py spend-hotspots --profile your-profile
```

This highlights the top services and usage types so the scan can focus on the bill shape, not just the resource inventory.

### Step 3: Run Domain Scans

For each domain, run the AWS CLI commands listed in `plugins/aws-cost-saver/checks/all_checks.yaml`.

Example for Compute domain:
```
aws ec2 describe-instances --filters "Name=instance-state-name,Values=running"
aws ec2 describe-volumes --filters "Name=status,Values=available"
aws ec2 describe-addresses
aws ec2 describe-snapshots --owner-ids self
```

### Step 4: Analyze Results

Compare AWS responses against check criteria:
- **EC2-001**: Flag instances with CPU < 5% for 14+ days
- **EC2-012**: Flag unattached EBS volumes
- **NET-001**: Flag unassociated Elastic IPs
- etc.

### Step 5: Generate Report

Save findings to `findings.json`:
```json
{
  "metadata": {
    "account_id": "123456789012",
    "regions": ["us-east-1", "us-west-2"],
    "scan_date": "2024-01-19T10:00:00Z"
  },
  "summary": {
    "actual_monthly_spend": 4111.88,
    "spend_source": "AWS Cost Explorer (December 2025)",
    "total_potential_savings": 656.49
  },
  "findings": [
    {
      "check_id": "EC2-012",
      "resource_id": "vol-abc123",
      "title": "Unattached EBS Volume",
      "domain": "compute",
      "severity": "high",
      "monthly_savings": 50.00,
      "confidence": 95,
      "description": "Volume has been unattached for 30+ days",
      "recommendation": "Snapshot and delete this volume"
    }
  ]
}
```

Then generate markdown:
```bash
python main.py report --findings findings.json
```

## Check Summary (174 Total)

| Domain | Checks | Key Checks |
|--------|--------|------------|
| **Compute** | 27 | EC2 idle, Compute Optimizer ML rightsizing/idle, memory check, GP2→GP3, Graviton |
| **Storage** | 24 | S3 lifecycle, EBS unattached, CloudWatch Logs, Secrets Manager, CloudTrail data events |
| **Database** | 15 | RDS idle, over-provisioned, RI coverage |
| **Networking** | 19 | Unused EIPs, NAT data processing, data transfer breakdown, public IPv4, VPC endpoints, Route 53 |
| **Serverless** | 10 | Lambda memory, unused functions, ARM64 |
| **Reservations** | 12 | RI/SP coverage gaps, RI purchase recommendations, SP purchase recommendations |
| **Containers** | 16 | ECS/EKS idle, Fargate optimization, Spot opportunity, ECR lifecycle |
| **Advanced DBs** | 18 | Aurora, DocumentDB, Neptune, Redshift optimization |
| **Analytics** | 15 | SageMaker, EMR, OpenSearch, QuickSight |
| **Data Pipelines** | 12 | Kinesis, MSK, Glue, EventBridge |
| **Storage Advanced** | 6 | FSx, AWS Backup optimization |

## Project Structure

```
claude-aws-cost-saver/
├── .claude-plugin/
│   └── marketplace.json         # Plugin marketplace definition (Claude Code)
├── .agents/
│   └── plugins/marketplace.json # Plugin marketplace definition (Codex)
├── plugins/
│   └── aws-cost-saver/          # The plugin (everything a marketplace install ships)
│       ├── .claude-plugin/
│       │   └── plugin.json      # Plugin manifest (Claude Code)
│       ├── .codex-plugin/
│       │   └── plugin.json      # Plugin manifest (Codex)
│       ├── .mcp.json            # MCP server configuration (AWS API)
│       ├── agents/
│       │   └── aws-cost-saver.md
│       ├── commands/
│       │   └── scan.md
│       ├── checks/
│       │   └── all_checks.yaml  # All 174 check definitions
│       ├── hooks/
│       │   └── hooks.json       # PreToolUse read-only guard
│       ├── scripts/
│       │   ├── start-mcp.sh     # Launches the AWS API MCP server via uvx
│       │   └── guard_readonly.py # Denies non-read AWS CLI operations
│       └── skills/
│           ├── reviewing-findings/
│           │   ├── SKILL.md
│           │   ├── REVIEW_CRITERIA.md
│           │   └── scripts/review_findings.py
│           └── validating-aws-pricing/
│               ├── SKILL.md
│               ├── PRICING_REFERENCE.md
│               └── scripts/validate_pricing.py
├── src/
│   ├── analysis/spend_hotspots.py
│   ├── outputs/markdown_report.py
│   └── parsers/cur_parser.py
├── CLAUDE.md                    # This file
├── README.md                    # Documentation
├── AGENTS.md                    # Codex setup guide
├── main.py                      # CLI (checks, report, spend-hotspots)
├── findings.json                # Scan results (generated)
└── resources.json               # Resource inventory (generated)
```

## Custom Subagent: aws-cost-saver

A specialized subagent for scanning AWS accounts. Located at `plugins/aws-cost-saver/agents/aws-cost-saver.md`.

### Features
- Reads check definitions from `plugins/aws-cost-saver/checks/all_checks.yaml`
- Executes AWS CLI commands via MCP tool
- **Saves raw resource inventory** from AWS API responses
- Analyzes results against thresholds
- Outputs both **resources + findings** in JSON format

### Output Format

Each domain scan returns:
```json
{
  "domain": "compute",
  "region": "us-east-1",
  "scanned_at": "2026-01-19T15:00:00Z",
  "resources": {
    "ec2_instances": [...],
    "ebs_volumes": [...]
  },
  "resource_summary": {
    "total_resources": 17,
    "estimated_monthly_cost": 450.00
  },
  "findings": [...]
}
```

This enables:
- "Scanned 45 resources, found 12 issues"
- "Potential savings: $556/mo (22% of total spend)"

### Parallel Domain Scanning

Scan all 11 domains simultaneously for faster analysis:

```
Scan the AWS account in parallel using the aws-cost-saver:aws-cost-saver agent:
- Launch 11 agents, one for each domain
- Domains: compute, storage, database, networking, serverless, reservations, containers, advanced_databases, analytics, data_pipelines, storage_advanced
- Region: us-east-1
```

Claude Code will invoke the Task tool 11 times in parallel:
```python
# Internal invocation (automatic)
Task(
    subagent_type="aws-cost-saver:aws-cost-saver",
    prompt="Scan the compute domain in us-east-1. Read checks from plugins/aws-cost-saver/checks/all_checks.yaml...",
)
# ... repeated for each domain
```

### Single Domain Scan

For focused analysis:
```
Use the aws-cost-saver agent to scan just the database domain
```

### Manual Invocation

You can also explicitly request parallel scanning:
```
Run 11 aws-cost-saver agents in parallel:
1. Compute domain (includes Compute Optimizer ML checks)
2. Storage domain (includes Secrets Manager, CloudTrail data events)
3. Database domain
4. Networking domain (includes data transfer analysis, Route 53)
5. Serverless domain
6. Reservations domain (includes purchase recommendations)
7. Containers domain (includes ECR lifecycle)
8. Advanced Databases domain
9. Analytics domain
10. Data Pipelines domain
11. Storage Advanced domain

Region: us-east-1
After all complete, merge findings into findings.json
```

## AWS MCP Commands Reference

### Compute
```bash
aws ec2 describe-instances
aws ec2 describe-volumes
aws ec2 describe-addresses
aws ec2 describe-snapshots --owner-ids self
aws ec2 describe-images --owners self
aws autoscaling describe-auto-scaling-groups
aws compute-optimizer get-enrollment-status
aws compute-optimizer get-ec2-instance-recommendations
aws compute-optimizer get-ec2-instance-recommendations --filters name=Finding,values=Idle
aws cloudwatch list-metrics --namespace CWAgent --metric-name mem_used_percent --dimensions Name=InstanceId,Value={id}
```

### Storage
```bash
aws s3api list-buckets
aws s3api get-bucket-lifecycle-configuration --bucket {name}
aws efs describe-file-systems
aws logs describe-log-groups
aws cloudtrail describe-trails
aws cloudtrail get-event-selectors --trail-name {trail_name}
aws secretsmanager list-secrets
aws secretsmanager describe-secret --secret-id {secret_id}
```

### Database
```bash
aws rds describe-db-instances
aws rds describe-db-snapshots --snapshot-type manual
aws rds describe-reserved-db-instances
aws dynamodb list-tables
aws dynamodb describe-table --table-name {name}
aws elasticache describe-cache-clusters
```

### Networking
```bash
aws ec2 describe-nat-gateways
aws elbv2 describe-load-balancers
aws elbv2 describe-target-groups
aws ec2 describe-vpc-endpoints
aws ec2 describe-vpcs
aws route53 list-hosted-zones
aws route53 list-resource-record-sets --hosted-zone-id {zone_id}
aws ce get-cost-and-usage --time-period Start={30d_ago},End={now} --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=USAGE_TYPE --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute"]}}'
```

### Serverless
```bash
aws lambda list-functions
aws apigateway get-rest-apis
aws apigatewayv2 get-apis
aws sqs list-queues
aws stepfunctions list-state-machines
```

### Reservations
```bash
aws ce get-reservation-coverage --time-period Start={30d_ago},End={now}
aws ce get-reservation-utilization --time-period Start={30d_ago},End={now}
aws ce get-savings-plans-coverage --time-period Start={30d_ago},End={now}
aws savingsplans describe-savings-plans
aws ec2 describe-reserved-instances
aws ce get-reservation-purchase-recommendation --service "Amazon Elastic Compute Cloud - Compute" --term-in-years ONE_YEAR --payment-option PARTIAL_UPFRONT --lookback-period-in-days SIXTY_DAYS
aws ce get-savings-plans-purchase-recommendation --savings-plans-type COMPUTE_SP --term-in-years ONE_YEAR --payment-option PARTIAL_UPFRONT --lookback-period-in-days SIXTY_DAYS
```

### Containers
```bash
aws ecs list-clusters
aws ecs list-services --cluster {cluster_arn}
aws ecs describe-services --cluster {cluster_arn} --services {service_arn}
aws ecs list-task-definitions
aws ecs describe-task-definition --task-definition {task_def}
aws eks list-clusters
aws eks list-nodegroups --cluster-name {cluster}
aws eks describe-nodegroup --cluster-name {cluster} --nodegroup-name {nodegroup}
aws ecr describe-repositories
aws ecr get-lifecycle-policy --repository-name {repo_name}
aws ecr describe-images --repository-name {repo_name} --filter tagStatus=UNTAGGED
```

### Advanced Databases
```bash
aws rds describe-db-clusters --filters "Name=engine,Values=aurora-mysql,aurora-postgresql"
aws docdb describe-db-clusters
aws docdb describe-db-instances
aws neptune describe-db-clusters
aws neptune describe-db-instances
aws redshift describe-clusters
aws opensearch list-domain-names
aws opensearch describe-domain --domain-name {domain}
```

### Analytics & ML
```bash
aws sagemaker list-notebook-instances
aws sagemaker list-endpoints
aws sagemaker describe-endpoint --endpoint-name {endpoint}
aws emr list-clusters --active
aws emr describe-cluster --cluster-id {id}
aws emr list-instance-groups --cluster-id {id}
aws quicksight list-data-sets --aws-account-id {id}
```

### Data Pipelines
```bash
aws kinesis list-streams
aws kinesis describe-stream-summary --stream-name {stream}
aws firehose list-delivery-streams
aws kafka list-clusters
aws kafka describe-cluster --cluster-arn {arn}
aws glue list-jobs
aws glue get-job --name {job}
aws events list-rules
aws events list-event-buses
```

### Storage Advanced
```bash
aws fsx describe-file-systems
aws backup list-backup-plans
aws backup list-backup-vaults
aws backup list-recovery-points-by-backup-vault --backup-vault-name {vault}
```

### Metrics (CloudWatch)
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value={id} \
  --start-time {14d_ago} \
  --end-time {now} \
  --period 86400 \
  --statistics Average
```

## Finding Format

Each finding should include:
```json
{
  "check_id": "EC2-001",
  "resource_id": "i-abc123",
  "title": "Idle EC2 Instance",
  "domain": "compute",
  "severity": "high",
  "category": "idle",
  "monthly_savings": 150.00,
  "confidence": 90,
  "description": "Instance has <5% CPU utilization for 14+ days",
  "recommendation": "Consider terminating or stopping this instance",
  "details": {
    "instance_type": "m5.large",
    "avg_cpu": 2.3,
    "days_monitored": 21
  }
}
```

## Confidence Scoring

| Score | Meaning | Action |
|-------|---------|--------|
| **≥70%** | Likely correct | Approved |
| **50-69%** | Needs validation | Flag for review |
| **<50%** | Insufficient data | Filter out |

Quick adjustments (2 only):
- **-30%** if resource < 7 days old
- **±10%** based on environment (production = -10%, dev/test = +10%)

## CUR File Support (Optional)

For offline analysis with Cost and Usage Report files:

```python
from src.parsers.cur_parser import CURParser

parser = CURParser()
data = parser.parse('path/to/cur.parquet')
```

Supported formats: Parquet (preferred), CSV

## Severity Levels

| Level | Action | Examples |
|-------|--------|----------|
| **Critical** | Immediate action | Unused resources with high cost |
| **High** | Action within 1 week | Idle instances, unattached volumes |
| **Medium** | Action within 1 month | Over-provisioned, previous gen |
| **Low** | Consider when convenient | Minor optimizations |
| **Info** | Awareness only | Compliance, tagging |

## Scan Workflow (9 Steps)

```
1. Discover account & regions
2. Ask compliance (HIPAA, SOC2, PCI-DSS)
3. Check Compute Optimizer enrollment (free ML rightsizing)
4. Get actual monthly spend + data transfer breakdown from Cost Explorer
5. Parallel domain scan (11 agents)
6. Quick review (2 checks: resource age, environment)
7. MANDATORY: Price validation (sanity check all findings)
8. Generate report
9. Show top findings & ask what to implement
```

### Quick Review (Inline)

Apply 2 adjustments only (no scripts):
- **Resource Age:** -30% confidence if < 7 days old
- **Environment:** -10% for production, +10% for dev/test

Mark findings:
- `approved` if confidence ≥ 70%
- `needs_validation` if confidence 50-69%
- `filtered` if confidence < 50%

### Safety Checks (Applied by Subagents)

Each `aws-cost-saver` subagent applies these checks internally before flagging resources:

1. **Multi-Signal Detection** - idle_score >= 0.60 required
2. **Batch Detection** - Skip if avg < 15% AND max > 60% AND ratio >= 4
3. **Dependency Checks** - Skip ASG members, NAT with routes, ELB with targets
4. **Cost-Tiered Confidence** - HIGH cost (>$100) needs 7+ days and 85% confidence
5. **Tag Exclusions** - Honor SkipCostOpt=true tag
6. **Zero Hallucination Pricing** - API first → verified table → $0 with pricing_unknown

See `agents/aws-cost-saver.md` for implementation details.

### MANDATORY: Zero Hallucination Pricing

**Every finding's `monthly_savings` must come from a verifiable source. NEVER guess.**

The subagent uses a strict 3-step pricing resolution:

```
Step 1: Query AWS Pricing API (aws pricing get-products) for the EXACT SKU
        → pricing_source: "aws_pricing_api"
Step 2: Fallback to verified table (us-east-1 only, exact match only)
        → pricing_source: "verified_table"
Step 3: Set monthly_savings=0, pricing_unknown=true
        → NEVER estimate, interpolate, or guess
```

**Anti-Hallucination Rules (enforced by subagent):**

| Rule | What It Prevents |
|------|-----------------|
| API first, table second, zero third | Guessing prices for unknown instance types |
| Missing CloudWatch data ≠ zero | False "idle" findings from missing metrics |
| Same family, one size down only | Creative downsize recommendations (m5→t3) |
| Fixed migration rates only | "Up to 40%" becoming fabricated savings |
| No data transfer composition guessing | "60% is probably S3 traffic" fabrication |
| Correct OS (Windows ≠ Linux) | 2× pricing errors on Windows instances |
| Correct deployment (Multi-AZ ≠ Single-AZ) | 2× pricing errors on Multi-AZ RDS |
| All EBS components (storage + IOPS + throughput) | io2 priced at $12 instead of $1,052 |
| Savings ≤ service spend (context-aware) | Finding saving more than the service costs |
| $1 minimum floor | Noise findings cluttering the report |
| RI/SP coverage check | On-Demand price used for RI-covered resources |
| Spot instance detection | On-Demand price used for Spot instances |
| Burstable credit analysis | t2/t3 flagged idle without credit check |
| IaC-managed resource detection | Deleting CFN/TF resource triggers recreation |
| Downsize floor check | Recommending nonexistent smaller size |
| Pricing API error handling | Graceful retry then fallback |
| Cost Explorer availability | All validations skip if CE unavailable |

### MANDATORY: Price Validation (Post-Scan)

**CRITICAL** - Run BEFORE generating the report:

1. **Sanity Check:** `finding.monthly_savings <= service.monthly_spend`
   - Example: If CloudWatch costs $159/mo, a finding CANNOT save $594/mo

2. **Verify Formulas:** Each finding type has a specific formula:
   | Finding | Formula |
   |---------|---------|
   | CW Logs Retention | `stored_gb × $0.03` (storage only, NOT $0.50 ingestion) |
   | Unattached EBS | `(size_gb × price_per_gb) + IOPS cost + throughput cost` |
   | Idle EC2 | `hourly_rate × 730` (from Pricing API, correct OS) |
   | Idle RDS | `hourly_rate × 730` (correct engine + deployment option) |

3. **For findings > $100:** Query usage-type breakdown:
   ```bash
   aws ce get-cost-and-usage \
     --filter '{"Dimensions": {"Key": "SERVICE", "Values": ["AmazonCloudWatch"]}}' \
     --group-by Type=DIMENSION,Key=USAGE_TYPE
   ```

4. **Verify pricing_source:** Every finding must have one of:
   - `aws_pricing_api` — price came from Pricing API query
   - `verified_table` — price came from hardcoded table (us-east-1 only)
   - `aws_cost_explorer` — price came from Cost Explorer (RI/SP recommendations)
   - `pricing_unknown` — price could not be determined, monthly_savings=0

5. **Correct & Flag:** If a finding fails validation:
   - Recalculate with correct formula
   - Add `pricing_corrected: true` to details
   - Document the correction reason

### Optional: Deep Review

For thorough analysis, run the review script:
```bash
python plugins/aws-cost-saver/skills/reviewing-findings/scripts/review_findings.py \
  reports/findings_{profile}.json --profile {profile}
```
