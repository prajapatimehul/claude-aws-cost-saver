---
name: validating-aws-pricing
description: MANDATORY validation of AWS cost findings (findings.json) before any report is generated. Cross-checks every monthly_savings estimate against the AWS Pricing API, the verified pricing table, and actual Cost Explorer billing. Catches errors like storage vs ingestion price confusion, missing IOPS components, wrong OS or deployment option, and savings that exceed service spend.
allowed-tools:
  - Read
  - Write
  - Bash
  - Grep
  - mcp__plugin_aws-cost-saver_awslabs-aws-api__call_aws
  - mcp__awslabs-aws-api__call_aws
---

# Validating AWS Pricing

**MANDATORY** validation step that catches pricing errors before they reach the user.

## Why This Matters

Common errors this skill catches:
- CloudWatch Logs: Confusing $0.50/GB ingestion with $0.03/GB storage
- Savings exceeding actual service spend (impossible)
- Wrong multipliers or formulas
- Missing cost components

## Purpose

This skill validates pricing for ALL findings using the Zero Hallucination Pricing System. Every finding must have a verifiable `pricing_source`. Findings without a pricing source or with fabricated prices are rejected (set to monthly_savings=0 with pricing_unknown=true).

## Quick Start

```bash
# Validate with default $100 threshold (queries API only for >$100 findings)
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_pricing.py" findings.json --profile your-profile

# Lower threshold to validate more findings
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_pricing.py" findings.json --profile your-profile --threshold 50

# Works with any AWS auth method (SSO, access keys, IAM role)
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_pricing.py" findings.json  # uses default credentials
```

## What Gets Updated

For findings **above threshold** (default $100):
1. Queries real AWS Pricing API for EC2, RDS, EBS
2. Updates `monthly_savings` with validated value
3. Marks `api_validated: true` in metadata

For findings **below threshold**:
1. Preserves pricing that already carries a compliant `pricing_source` and `calculation`
2. Resolves from the verified us-east-1 table where an exact formula exists
3. Otherwise sets `monthly_savings=0` with `pricing_unknown=true` — it NEVER estimates

All findings get `pricing_validated` metadata showing the source.

## Pricing Calculation by Finding Type

### Idle Resources (EC2-001, etc.)
Savings = Full monthly cost of the resource
```python
savings = hourly_rate * 730  # Resource should be terminated
```

### Unattached Storage (EC2-012, EBS-001)
Savings = Storage cost per month
```python
savings = size_gb * price_per_gb  # e.g., 100GB * $0.08 = $8.00
```

### Over-provisioned (RDS-002, LAMBDA-001)
Savings = Difference between current and recommended size
```python
current_cost = current_hourly * 730
recommended_cost = recommended_hourly * 730
savings = current_cost - recommended_cost
```

### No RI Coverage (RDS-005)
Savings = On-Demand cost - Reserved Instance cost
```python
on_demand_monthly = hourly_rate * 730
ri_monthly = ri_upfront / 12 + ri_hourly * 730
savings = on_demand_monthly - ri_monthly  # ~40-60% typically
```

### CloudWatch Logs (LOG-001)
Savings = Storage cost that can be avoided with retention policy
```python
savings = stored_gb * 0.03  # $0.03 per GB-month
```

## AWS Pricing Reference

Current pricing (us-east-1):

| Resource | Price | Unit | Monthly (730 hrs) |
|----------|-------|------|-------------------|
| t2.nano | $0.0058 | /hour | $4.23 |
| t3.nano | $0.0052 | /hour | $3.80 |
| gp3 storage | $0.08 | /GB-month | - |
| db.r5.xlarge | $0.50 | /hour | $365.00 |
| cache.t3.small (Valkey) | $0.0272 | /hour | $19.86 |
| CloudWatch Logs | $0.03 | /GB-month | - |

For complete pricing, see [PRICING_REFERENCE.md](PRICING_REFERENCE.md).

## Output

The script updates `findings.json` in place:

```json
{
  "check_id": "EC2-001",
  "monthly_savings": 4.23,
  "pricing_validated": {
    "source": "AWS Pricing API",
    "validated_at": "2026-01-19T10:00:00Z",
    "hourly_rate": 0.0058,
    "calculation": "0.0058 * 730 hours"
  }
}
```

## Workflow

1. Load `findings.json`
2. For each finding:
   - Identify resource type from `check_id` and `details`
   - Query AWS Pricing API (or use cached known prices)
   - Calculate correct savings based on finding category
   - Update `monthly_savings` field
   - Add `pricing_validated` metadata
3. Recalculate `total_monthly_savings` in metadata
4. Save updated `findings.json`

## Task Checklist

```
- [ ] Load findings.json
- [ ] Get actual billing from Cost Explorer (by service AND usage type)
- [ ] ANTI-HALLUCINATION CHECKS (run first):
  - [ ] Every finding has details.pricing_source (reject if missing)
  - [ ] Every finding with savings > 0 has details.calculation (reject if missing)
  - [ ] No findings have savings > 0 with pricing_source="pricing_unknown" (reject if found)
  - [ ] No idle findings count missing metrics as zero activity (reject if found)
  - [ ] Downsize targets are same-family-one-size-down or from Compute Optimizer (reject if not)
- [ ] For EACH finding:
  - [ ] Verify formula matches finding type
  - [ ] Check savings <= service spend (sanity check)
  - [ ] Query AWS Pricing API for ALL findings with pricing_source != "aws_pricing_api"
  - [ ] Check OS matches (Windows vs Linux) for EC2 findings
  - [ ] Check deployment option matches (Multi-AZ vs Single-AZ) for RDS findings
  - [ ] Check all EBS components (storage + IOPS + throughput) for EBS findings
  - [ ] Recalculate if errors found
  - [ ] Add calculation breakdown to details
- [ ] Flag any corrected findings with pricing_corrected: true
- [ ] Set any unfixable findings to monthly_savings=0 with pricing_unknown=true
- [ ] Recalculate total savings in metadata
- [ ] Save corrected findings.json
- [ ] Regenerate report with accurate prices
```

---

## CRITICAL: Sanity Check Rules

### Rule 1: Savings Cannot Exceed Service Spend

```python
assert finding.monthly_savings <= service_monthly_spend, \
    f"Finding {finding.check_id} claims ${finding.monthly_savings} but service only costs ${service_monthly_spend}"
```

**Example Failure:**
- Finding: CloudWatch Logs retention saves $594/mo
- Billing: CloudWatch total spend = $159/mo
- **INVALID** - immediately flag and recalculate

### Rule 2: Use Correct Cost Component

Many AWS services have MULTIPLE cost components:

| Service | Components | What Retention Affects |
|---------|------------|------------------------|
| **CloudWatch Logs** | Ingestion ($0.50/GB) + Storage ($0.03/GB) | Storage ONLY |
| **S3** | Storage + Requests + Transfer | Storage + old versions |
| **EBS** | Storage + IOPS + Throughput | Storage ONLY |
| **RDS** | Compute + Storage + I/O | Depends on finding |

### Rule 3: Verify With Usage Type Breakdown

For any finding > $100, get usage-type breakdown:

```bash
aws ce get-cost-and-usage --profile {profile} \
  --time-period Start={LAST_MONTH_START},End={LAST_MONTH_END} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Dimensions": {"Key": "SERVICE", "Values": ["AmazonCloudWatch"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE
```

This shows:
- `DataProcessing-Bytes`: Log ingestion ($0.50/GB)
- `TimedStorage-ByteHrs`: Log storage ($0.03/GB)

**Only storage can be reduced by retention policy!**

---

## Common Pricing Mistakes & Corrections

### CloudWatch Logs Retention

**WRONG:**
```python
savings = stored_gb * 0.50  # Using ingestion price!
# 221 GB * $0.50 = $110.50  # WRONG!
```

**CORRECT:**
```python
savings = stored_gb * 0.03  # Storage price
# 221 GB * $0.03 = $6.63    # CORRECT!
```

**Even more correct (check what % is older than retention):**
```python
# If setting 90-day retention, only logs older than 90 days are deleted
# Estimate 50% of stored data is older than 90 days
savings = stored_gb * 0.03 * 0.5
```

### Unattached EBS Volume

**CORRECT:**
```python
savings = size_gb * price_per_gb
# gp3: 100 GB * $0.08 = $8.00
# gp2: 100 GB * $0.10 = $10.00
```

### Over-provisioned RDS

**CORRECT:**
```python
current_cost = current_hourly * 730
recommended_cost = recommended_hourly * 730
savings = current_cost - recommended_cost

# db.r5.xlarge -> db.r5.large
# $0.50 * 730 - $0.25 * 730 = $365 - $182.50 = $182.50
```

---

## Validation Output Format

After validation, each finding should include:

```json
{
  "check_id": "LOG-001",
  "monthly_savings": 6.64,
  "pricing_validated": {
    "validated_at": "2026-01-19T12:00:00Z",
    "original_estimate": 594.34,
    "corrected": true,
    "correction_reason": "Used storage price ($0.03/GB) instead of ingestion price ($0.50/GB)",
    "calculation": "221.4 GB × $0.03/GB = $6.64",
    "sanity_check": {
      "service": "AmazonCloudWatch",
      "service_spend": 159.13,
      "finding_savings": 6.64,
      "passed": true
    }
  }
}
```
