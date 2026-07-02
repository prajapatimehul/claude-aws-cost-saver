---
name: reviewing-findings
description: Reviews AWS cost optimization findings (findings.json) for accuracy, validates recommendations, and filters false positives using confidence-based scoring. Use after an AWS cost scan to adjust confidence for resource age, environment, ASG membership, DR/standby naming, and IaC management, and to mark each finding approved, needs_validation, or filtered before reporting.
allowed-tools:
  - Read
  - Write
  - Bash
  - Grep
  - Glob
  - Task
  - mcp__plugin_aws-cost-saver_awslabs-aws-api__call_aws
  - mcp__awslabs-aws-api__call_aws
---

# Reviewing AWS Cost Findings

Multi-perspective review of cost optimization findings with confidence-based filtering.

## Quick Start

```bash
# Review findings and update findings.json in place
python3 "${CLAUDE_PLUGIN_ROOT}/skills/reviewing-findings/scripts/review_findings.py" findings.json --profile your-profile

# Re-review findings that already carry a review_status
python3 "${CLAUDE_PLUGIN_ROOT}/skills/reviewing-findings/scripts/review_findings.py" findings.json --force
```

For the detailed per-check confidence matrices and false-positive patterns, read
[REVIEW_CRITERIA.md](REVIEW_CRITERIA.md) in this skill's directory.

## Review Process

### 1. Pre-flight Checks

Skip review if:
- No findings.json exists
- Findings already reviewed (has `review_status`)
- Empty findings array

### 2. Multi-Agent Review (4 Parallel Agents)

Launch 4 independent review agents:

```
Agent #1: Resource Verification
├── Verify resource still exists
├── Check current utilization metrics
└── Confirm finding is still valid

Agent #2: Recommendation Quality
├── Validate recommendation is actionable
├── Check for edge cases (ASG, DR, scheduled)
└── Verify savings calculation logic

Agent #3: Business Context
├── Identify environment (prod/dev/staging)
├── Check for dependencies
└── Flag potential risks

Agent #4: Historical Pattern
├── Check for burst patterns
├── Identify seasonal usage
└── Review recent changes
```

### 3. Confidence Scoring

Each agent assigns confidence (0-100):

| Score | Meaning |
|-------|---------|
| 90-100 | Definite savings - act immediately |
| 70-89 | High confidence - safe to implement |
| 50-69 | Medium confidence - needs validation |
| 25-49 | Low confidence - likely false positive |
| 0-24 | Skip - insufficient evidence |

**Filter threshold: 50** (adjustable)

### 4. Update Findings

Add review metadata to each finding:

```json
{
  "check_id": "EC2-001",
  "monthly_savings": 150.00,
  "review_status": {
    "reviewed_at": "2026-01-19T15:00:00Z",
    "final_confidence": 85,
    "agents": {
      "resource_verification": 90,
      "recommendation_quality": 80,
      "business_context": 85,
      "historical_pattern": 85
    },
    "action": "approved",
    "notes": "Resource verified idle for 21 days"
  }
}
```

## Review Criteria by Check Type

### Idle Resources (EC2-001, RDS-001)

**Verify:**
- [ ] CPU/memory metrics for 14+ days
- [ ] Network activity
- [ ] Not part of ASG or scheduled scaling
- [ ] Not a standby/DR instance

**Red flags:**
- Part of Auto Scaling Group → -30 confidence
- Created < 14 days ago → -20 confidence
- Has "dr" or "standby" in name → -25 confidence

### Over-provisioned (EC2-002, RDS-002, LAMBDA-001)

**Verify:**
- [ ] Peak utilization is below threshold
- [ ] No recent high-utilization spikes
- [ ] Recommended size handles peak + buffer

**Red flags:**
- Peak CPU > 60% → -20 confidence
- Burst workload pattern → -25 confidence
- Memory not checked → -15 confidence

### Unattached Storage (EC2-012, EBS-001)

**Verify:**
- [ ] Volume truly unattached (not just unmounted)
- [ ] No recent attachment activity
- [ ] Not a backup volume

**Red flags:**
- Detached < 7 days ago → -30 confidence
- Has snapshot → lower urgency
- Size > 500GB → verify with owner

### Reserved Instance Coverage (RDS-005, RI-001)

**Verify:**
- [ ] Workload is steady (not variable)
- [ ] Commitment period acceptable
- [ ] Instance type likely to remain same

**Red flags:**
- Variable workload → -25 confidence
- Pending migration → -40 confidence
- Dev/test environment → -20 confidence

## Agent Implementation

### Agent #1: Resource Verification

```
Prompt: For each finding in findings.json, verify the resource:
1. Call AWS API to check resource exists
2. Get current utilization metrics (last 7 days)
3. Compare current state vs finding state
4. Score confidence based on verification

Output JSON with:
- resource_verified: boolean
- current_metrics: object
- confidence: 0-100
- notes: string
```

### Agent #2: Recommendation Quality

```
Prompt: For each finding, evaluate the recommendation:
1. Is the recommendation actionable?
2. Are there edge cases not considered?
3. Is the savings calculation reasonable?
4. Are there dependencies to consider?

Score based on:
- Clear next steps: +20
- Edge cases addressed: +15
- Accurate savings: +25
- No dependencies: +20
- Reasonable effort: +20
```

### Agent #3: Business Context

```
Prompt: For each finding, assess business context:
1. Identify environment (check name, tags)
2. Check for production dependencies
3. Assess risk of implementing recommendation
4. Consider compliance requirements

Adjust confidence:
- Production resource: -10 (needs careful review)
- Has dependencies: -20
- Compliance-related: -15
- Dev/test: +10 (lower risk)
```

### Agent #4: Historical Pattern

```
Prompt: For each finding, analyze patterns:
1. Check CloudWatch metrics for patterns
2. Identify burst/scheduled workloads
3. Review resource modification history
4. Check for seasonal patterns

Red flags:
- Burst pattern detected: -25
- Recent scaling event: -20
- Seasonal variation: -15
- Consistent low usage: +15
```

## Output Format

### Terminal Output

```
## Findings Review

Reviewed 35 findings. Results:

✓ 25 findings APPROVED (confidence ≥70)
⚠ 6 findings NEEDS VALIDATION (confidence 50-69)
✗ 4 findings FILTERED (confidence <50)

### Top Approved Findings

1. **RDS-002**: Over-provisioned RDS Instance
   Resource: production-clinical-trial-matcher
   Savings: $95.00/month
   Confidence: 85%
   Notes: CPU avg 2.6% for 31 days, no burst patterns

2. **EC2-012**: Unattached EBS Volume
   Resource: vol-0f282561946f02d6a
   Savings: $8.00/month
   Confidence: 92%
   Notes: Unattached for 45 days, has snapshot backup

### Needs Validation

1. **LAMBDA-001**: Memory Over-provisioning
   Resource: production-file-handler
   Confidence: 55%
   Issue: Only 78 invocations - insufficient data
   Action: Monitor for 14 more days

### Filtered (False Positives)

1. **EC2-001**: Idle EC2 Instance
   Resource: i-0885726ca0d3e7856
   Original confidence: 75%
   Final confidence: 35%
   Reason: Part of ASG, scheduled scaling detected
```

### Updated findings.json

Each finding gets a `review_status` object added.

## Workflow

1. Read `findings.json`
2. Launch 4 parallel review agents (use Task tool)
3. Collect agent results
4. Calculate final confidence (average of 4 agents)
5. Apply filter threshold (50)
6. Update findings with review status
7. Generate summary report
8. Save updated `findings.json`

## Task Checklist

```
- [ ] Load findings.json
- [ ] Launch Agent #1: Resource Verification
- [ ] Launch Agent #2: Recommendation Quality
- [ ] Launch Agent #3: Business Context
- [ ] Launch Agent #4: Historical Pattern
- [ ] Merge agent results
- [ ] Calculate final confidence scores
- [ ] Apply filter threshold
- [ ] Update findings with review_status
- [ ] Save updated findings.json
- [ ] Output summary to terminal
```
