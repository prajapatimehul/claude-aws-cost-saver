# Review Criteria Reference

Detailed criteria for reviewing each finding type.

## Confidence Adjustments

### Universal Adjustments

| Factor | Adjustment |
|--------|------------|
| Resource < 7 days old | -40 |
| Resource 7-14 days old | -20 |
| Part of Auto Scaling Group | -30 |
| Has "dr", "standby", "backup" in name | -25 |
| Has "dev", "test", "staging" in name | +10 |
| Production resource | -10 |
| Multiple metrics agree | +15 |
| Single metric only | -10 |
| Recent configuration change | -20 |
| Consistent pattern (14+ days) | +20 |

### Anti-Hallucination Adjustments (MANDATORY - check first)

| Factor | Action |
|--------|--------|
| `pricing_source` missing from details | **REJECT** → set monthly_savings=0, pricing_unknown=true |
| `calculation` missing from details | **REJECT** → set monthly_savings=0, pricing_unknown=true |
| pricing_source="pricing_unknown" but savings > 0 | **REJECT** → set monthly_savings=0 |
| Missing CloudWatch data counted as zero | **REJECT** → recalculate idle score without that signal |
| Downsize target crosses instance family | **REJECT** → set monthly_savings=0, needs manual review |
| Windows instance priced as Linux | **REJECT** → re-query Pricing API with operatingSystem=Windows |
| Multi-AZ RDS priced as Single-AZ | **REJECT** → re-query Pricing API with deploymentOption=Multi-AZ |
| EBS savings missing IOPS/throughput costs | **REJECT** → recalculate with all 3 components |
| monthly_savings > service spend (3+ resources) | **REJECT** → cap at 90% of service spend |
| monthly_savings > 110% service spend (any count) | **REJECT** → recalculate, formula is wrong |
| Migration savings using "up to" percentages | **REJECT** → use fixed conservative rates only |
| Spot instance priced at On-Demand rate | **REJECT** → use On-Demand × 0.3 for Spot |
| RI/SP-covered resource at On-Demand price | **FLAG** → reduce confidence by -20%, note coverage |
| IaC-managed resource recommended for deletion | **FLAG** → change to "Review with IaC team", -10% confidence |
| Burstable instance (t2/t3/t4g) without credit check | **FLAG** → reduce confidence by -10% |
| Downsize target doesn't exist (already at minimum) | **REJECT** → set monthly_savings=0 |
| `pricing_corrected: true` already in details | **SKIP** → already validated, no further adjustment |

**Valid `pricing_source` values:** `aws_pricing_api`, `verified_table`, `aws_cost_explorer`, `pricing_unknown`

### Starting Confidence

Each finding starts with its original confidence from the scan. Adjustments are applied based on review findings.

## Check-Specific Criteria

### EC2-001: Idle EC2 Instance

**Validation Steps:**
1. Query CloudWatch CPUUtilization for last 14 days
2. Query NetworkPacketsIn/Out
3. Check Auto Scaling Group membership
4. Review instance tags for environment

**Pass Criteria:**
- Average CPU < 5% for 14+ days
- Network activity < 1MB/day
- Not in ASG
- No scheduled scaling

**AWS Commands:**
```bash
# Check CPU
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value={instance_id} \
  --start-time {14_days_ago} \
  --end-time {now} \
  --period 86400 \
  --statistics Average Maximum

# Check ASG membership
aws autoscaling describe-auto-scaling-instances \
  --instance-ids {instance_id}

# Check tags
aws ec2 describe-tags \
  --filters "Name=resource-id,Values={instance_id}"
```

**Confidence Matrix:**
| CPU Avg | CPU Max | Network | Days | Confidence |
|---------|---------|---------|------|------------|
| <2% | <10% | <1MB | 21+ | 95 |
| <5% | <20% | <10MB | 14+ | 85 |
| <5% | <40% | <100MB | 14+ | 70 |
| <10% | <50% | Any | 7-14 | 55 |
| Any | >60% | Any | Any | 30 |

---

### EC2-002: Over-provisioned EC2 Instance

**Validation Steps:**
1. Query CPUUtilization (average AND maximum)
2. Query MemoryUtilization (if CW agent)
3. Review peak usage times
4. Check for burst patterns

**Pass Criteria:**
- Peak CPU < 40% for 14+ days
- Memory (if available) < 40%
- No burst patterns
- Recommended size handles 2x current peak

**Confidence Matrix:**
| Peak CPU | Memory | Pattern | Confidence |
|----------|--------|---------|------------|
| <20% | <30% | Steady | 90 |
| <30% | <40% | Steady | 80 |
| <40% | <50% | Steady | 70 |
| <40% | Any | Burst | 50 |
| >40% | Any | Any | 30 |

---

### EC2-012: Unattached EBS Volume

**Validation Steps:**
1. Confirm volume status is "available"
2. Check last attachment time
3. Verify no recent activity
4. Check for associated snapshots

**Pass Criteria:**
- Status = "available"
- Unattached for 7+ days
- No pending attachments

**AWS Commands:**
```bash
# Check volume status
aws ec2 describe-volumes \
  --volume-ids {volume_id}

# Check for snapshots
aws ec2 describe-snapshots \
  --filters "Name=volume-id,Values={volume_id}"

# Check CloudTrail for attachment events
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue={volume_id}
```

**Confidence Matrix:**
| Days Unattached | Has Snapshot | Size | Confidence |
|-----------------|--------------|------|------------|
| 30+ | Yes | Any | 95 |
| 30+ | No | <100GB | 90 |
| 14-30 | Yes | Any | 85 |
| 14-30 | No | Any | 75 |
| 7-14 | Any | Any | 60 |
| <7 | Any | Any | 30 |

---

### RDS-002: Over-provisioned RDS Instance

**Validation Steps:**
1. Query CPUUtilization for 14+ days
2. Query DatabaseConnections
3. Query FreeableMemory
4. Review peak patterns

**Pass Criteria:**
- Average CPU < 40% for 14+ days
- Peak CPU < 60%
- Connections stable (no growth trend)
- Recommended size handles peak

**AWS Commands:**
```bash
# CPU metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value={db_id} \
  --start-time {14_days_ago} \
  --end-time {now} \
  --period 86400 \
  --statistics Average Maximum

# Connections
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value={db_id} \
  --start-time {14_days_ago} \
  --end-time {now} \
  --period 86400 \
  --statistics Average Maximum
```

**Confidence Matrix:**
| CPU Avg | CPU Peak | Connections Trend | Confidence |
|---------|----------|-------------------|------------|
| <10% | <30% | Stable/Down | 90 |
| <20% | <40% | Stable | 80 |
| <30% | <50% | Stable | 70 |
| <40% | <60% | Up | 55 |
| Any | >60% | Any | 35 |

---

### RDS-005: No RI Coverage

**Validation Steps:**
1. Verify instance runs 24/7
2. Check for planned migrations
3. Assess workload stability
4. Verify commitment period acceptable

**Pass Criteria:**
- Instance running 24/7
- No planned instance type changes
- Workload stable for 3+ months
- Not a dev/test environment (unless long-lived)

**Confidence Matrix:**
| Uptime | Stability | Environment | Confidence |
|--------|-----------|-------------|------------|
| 24/7 | 90+ days | Production | 90 |
| 24/7 | 30-90 days | Production | 75 |
| 24/7 | Any | Dev (long-lived) | 60 |
| Variable | Any | Any | 40 |
| Any | <30 days | Any | 30 |

---

### LAMBDA-001: Memory Over-provisioning

**Validation Steps:**
1. Get function invocation count
2. Query duration metrics
3. Check memory utilization (if enabled)
4. Verify invocation count sufficient for analysis

**Pass Criteria:**
- 100+ invocations in analysis period
- Max memory used < 50% of allocated
- Duration doesn't increase with lower memory

**AWS Commands:**
```bash
# Invocations
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value={function_name} \
  --start-time {30_days_ago} \
  --end-time {now} \
  --period 2592000 \
  --statistics Sum

# Duration
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value={function_name} \
  --start-time {30_days_ago} \
  --end-time {now} \
  --period 86400 \
  --statistics Average Maximum
```

**Confidence Matrix:**
| Invocations | Memory Used % | Duration Variance | Confidence |
|-------------|---------------|-------------------|------------|
| 1000+ | <30% | Low | 90 |
| 500+ | <40% | Low | 80 |
| 100+ | <50% | Low | 70 |
| 50-100 | <50% | Low | 55 |
| <50 | Any | Any | 35 |

---

### LOG-001: CloudWatch Logs No Retention

**Validation Steps:**
1. Confirm no retention policy set
2. Calculate current storage size
3. Estimate savings from retention
4. Check for compliance requirements

**Pass Criteria:**
- No retention policy
- Storage > 1GB
- No compliance holds
- Logs older than needed retention

**Confidence Matrix:**
| Storage | Age of Logs | Compliance | Confidence |
|---------|-------------|------------|------------|
| >100GB | >90 days | None | 95 |
| >10GB | >90 days | None | 90 |
| >1GB | >30 days | None | 80 |
| <1GB | Any | None | 60 |
| Any | Any | HIPAA/SOC | 50 |

---

### S3-005: Excessive Versioning

**Validation Steps:**
1. Check versioning configuration
2. Count noncurrent versions
3. Review lifecycle policies
4. Estimate storage from versions

**Pass Criteria:**
- Versioning enabled
- No lifecycle for noncurrent versions
- Significant storage in old versions
- Not compliance-required retention

**Confidence Matrix:**
| Version Storage % | Has Lifecycle | Compliance | Confidence |
|-------------------|---------------|------------|------------|
| >50% | No | None | 90 |
| >30% | No | None | 80 |
| >20% | Partial | None | 70 |
| <20% | Any | None | 55 |
| Any | Any | Required | 40 |

---

## False Positive Patterns

### Automatically Filter These

1. **ASG Members**: Instances in Auto Scaling Groups often appear idle but scale on demand
2. **DR/Standby Resources**: Resources with "dr", "standby", "failover" in name
3. **Recently Created**: Resources < 7 days old have insufficient data
4. **Scheduled Workloads**: Resources with evening/weekend idle patterns
5. **Burst Workloads**: Resources with periodic high utilization spikes

### Require Manual Validation

1. **Production Resources**: Always flag for human review
2. **High-Value Savings**: Findings with >$500/month savings
3. **Cross-Account Dependencies**: Resources referenced by other accounts
4. **Compliance-Tagged**: Resources with compliance-related tags

---

## Batch Workload Detection

**CRITICAL**: Batch workloads show low AVERAGE but high PEAK utilization. These are NOT idle.

### Batch Pattern Definition

```
isBatch = cpuAvg < 15% AND cpuMax > 60% AND (cpuMax / cpuAvg) >= 4
```

### Examples

| Instance | Avg CPU | Max CPU | Ratio | Classification |
|----------|---------|---------|-------|----------------|
| i-abc123 | 5% | 85% | 17x | **BATCH** - skip |
| i-def456 | 3% | 12% | 4x | Truly idle - flag |
| i-ghi789 | 25% | 80% | 3.2x | Active - not idle |

### Required Metric Collection

```bash
# ALWAYS get both Average AND Maximum
aws cloudwatch get-metric-statistics --namespace AWS/EC2 \
  --metric-name CPUUtilization --dimensions Name=InstanceId,Value={id} \
  --start-time {14d_ago} --end-time {now} --period 86400 \
  --statistics Average Maximum
```

### When Batch Detected

```json
{
  "check_id": "EC2-001",
  "batch_detection": {
    "cpu_avg": 5.2,
    "cpu_max": 87.3,
    "ratio": 16.8,
    "is_batch": true
  },
  "status": "skipped",
  "skip_reason": "Batch workload pattern (avg 5.2%, max 87.3%, ratio 16.8x)"
}
```

---

## Review Actions

| Final Confidence | Action | Status |
|------------------|--------|--------|
| ≥80 | Recommend implementation | `approved` |
| 60-79 | Recommend with caveats | `approved_with_review` |
| 50-59 | Needs validation | `needs_validation` |
| <50 | Filter from report | `filtered` |

## Agent Weighting

When combining agent scores:

| Agent | Weight | Rationale |
|-------|--------|-----------|
| Resource Verification | 35% | Most critical - is finding still valid? |
| Recommendation Quality | 25% | Is the recommendation actionable? |
| Business Context | 25% | What's the risk of implementing? |
| Historical Pattern | 15% | Are there hidden patterns? |

**Final Confidence Formula:**
```python
final = (
    resource_verification * 0.35 +
    recommendation_quality * 0.25 +
    business_context * 0.25 +
    historical_pattern * 0.15
)
```
