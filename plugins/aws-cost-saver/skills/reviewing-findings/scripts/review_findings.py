#!/usr/bin/env python3
"""
AWS Cost Findings Review Script

Reviews findings.json and applies confidence adjustments based on:
- Resource age
- Environment (prod/dev/test)
- Pattern analysis
- Edge case detection

Usage:
    python review_findings.py findings.json --profile <aws-profile>
    python review_findings.py findings.json --profile <aws-profile> --threshold 60
"""

import json
import sys
import argparse
import shlex
import subprocess
import re
from datetime import datetime, timezone

# Confidence adjustments
ADJUSTMENTS = {
    "resource_new_7d": -40,
    "resource_new_14d": -20,
    "part_of_asg": -30,
    "dr_standby_name": -25,
    "dev_test_name": +10,
    "production": -10,
    "multiple_metrics": +15,
    "single_metric": -10,
    "recent_change": -20,
    "consistent_pattern": +20,
    "burst_pattern": -25,
    "insufficient_data": -30,
    "missing_pricing_source": -60,
    "iac_managed_delete": -10,
}

ALLOWED_PRICING_SOURCES = {
    "aws_pricing_api",
    "verified_table",
    "aws_cost_explorer",
    "pricing_unknown",
}

# Name patterns
DR_PATTERNS = re.compile(r'(dr|standby|backup|failover|replica)', re.IGNORECASE)
DEV_PATTERNS = re.compile(r'(dev|test|staging|sandbox|qa)', re.IGNORECASE)
PROD_PATTERNS = re.compile(r'(prod|production|live|prd)', re.IGNORECASE)


def run_aws_command(command: str, profile: str) -> dict | None:
    """Execute AWS CLI command and return JSON response."""
    args = shlex.split(command) + ["--output", "json"]
    if profile:
        args += ["--profile", profile]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        return None


def detect_environment(resource_id: str, details: dict) -> str:
    """Detect environment from resource name and tags."""
    # Check resource ID
    if DEV_PATTERNS.search(resource_id):
        return "development"
    if PROD_PATTERNS.search(resource_id):
        return "production"

    # Check tags if available
    tags = details.get("tags", {})
    env_tag = tags.get("Environment", tags.get("environment", tags.get("Env", "")))
    if env_tag:
        if DEV_PATTERNS.search(env_tag):
            return "development"
        if PROD_PATTERNS.search(env_tag):
            return "production"

    return "unknown"


def detect_dr_standby(resource_id: str) -> bool:
    """Check if resource appears to be DR/standby."""
    return bool(DR_PATTERNS.search(resource_id))


def is_iac_managed(details: dict) -> bool:
    """Detect common IaC markers in resource tags/details."""
    tags = details.get("tags", {})
    if isinstance(tags, list):
        normalized_tags = {}
        for tag in tags:
            if isinstance(tag, dict) and "Key" in tag and "Value" in tag:
                normalized_tags[tag["Key"]] = tag["Value"]
        tags = normalized_tags

    if not isinstance(tags, dict):
        tags = {}

    iac_keys = {
        "aws:cloudformation:stack-name",
        "aws:cloudformation:stack-id",
        "aws:cloudformation:logical-id",
        "terraform",
        "terraform_workspace",
        "eksctl.cluster.k8s.io/v1alpha1/cluster-name",
    }
    lower_keys = {key.lower() for key in tags.keys()}
    if any(key in lower_keys for key in iac_keys):
        return True

    return bool(details.get("managed_by_iac"))


def check_asg_membership(instance_id: str, profile: str) -> bool:
    """Check if EC2 instance is part of an Auto Scaling Group."""
    if not instance_id.startswith("i-"):
        return False

    result = run_aws_command(
        f"aws autoscaling describe-auto-scaling-instances --instance-ids {instance_id}",
        profile
    )

    if result and result.get("AutoScalingInstances"):
        return True
    return False


def check_resource_age(details: dict) -> int:
    """Return resource age in days, or -1 if unknown."""
    created = details.get("created_at") or details.get("launch_time") or details.get("create_time")
    if not created:
        return -1

    try:
        if isinstance(created, str):
            # Handle various date formats
            for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
                try:
                    created_dt = datetime.strptime(created, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                return -1
        else:
            return -1

        now = datetime.now(timezone.utc)
        age = (now - created_dt).days
        return age
    except Exception:
        return -1


def analyze_finding(finding: dict, profile: str, threshold: int = 50) -> dict:
    """Analyze a single finding and calculate confidence adjustments."""
    check_id = finding.get("check_id", "")
    resource_id = finding.get("resource_id", "")
    details = finding.get("details", {})
    original_confidence = finding.get("confidence", 70)

    adjustments_applied = []
    confidence = original_confidence
    notes = []

    pricing_source = details.get("pricing_source")
    if pricing_source not in ALLOWED_PRICING_SOURCES:
        confidence += ADJUSTMENTS["missing_pricing_source"]
        adjustments_applied.append(("missing_pricing_source", ADJUSTMENTS["missing_pricing_source"]))
        notes.append("Missing or invalid pricing_source metadata")
    elif pricing_source == "pricing_unknown" and finding.get("monthly_savings", 0) > 0:
        confidence += ADJUSTMENTS["missing_pricing_source"]
        adjustments_applied.append(("pricing_unknown_with_savings", ADJUSTMENTS["missing_pricing_source"]))
        notes.append("pricing_unknown cannot have savings greater than zero")

    # 1. Environment detection
    env = detect_environment(resource_id, details)
    if env == "development":
        confidence += ADJUSTMENTS["dev_test_name"]
        adjustments_applied.append(("dev_test_name", ADJUSTMENTS["dev_test_name"]))
        notes.append(f"Development environment detected")
    elif env == "production":
        confidence += ADJUSTMENTS["production"]
        adjustments_applied.append(("production", ADJUSTMENTS["production"]))
        notes.append(f"Production environment - requires careful review")

    # 2. DR/Standby detection
    if detect_dr_standby(resource_id):
        confidence += ADJUSTMENTS["dr_standby_name"]
        adjustments_applied.append(("dr_standby_name", ADJUSTMENTS["dr_standby_name"]))
        notes.append("DR/Standby resource - may be intentionally idle")

    # 3. Resource age check
    age_days = check_resource_age(details)
    if age_days >= 0:
        if age_days < 7:
            confidence += ADJUSTMENTS["resource_new_7d"]
            adjustments_applied.append(("resource_new_7d", ADJUSTMENTS["resource_new_7d"]))
            notes.append(f"Resource only {age_days} days old - insufficient data")
        elif age_days < 14:
            confidence += ADJUSTMENTS["resource_new_14d"]
            adjustments_applied.append(("resource_new_14d", ADJUSTMENTS["resource_new_14d"]))
            notes.append(f"Resource {age_days} days old - limited data")

    # 4. ASG membership (for EC2 idle checks)
    if check_id in ("EC2-001", "EC2-026") and resource_id.startswith("i-"):
        if check_asg_membership(resource_id, profile):
            confidence += ADJUSTMENTS["part_of_asg"]
            adjustments_applied.append(("part_of_asg", ADJUSTMENTS["part_of_asg"]))
            notes.append("Part of Auto Scaling Group - likely false positive")

    # 4b. Compute Optimizer findings get +15 confidence (ML-validated)
    if check_id in ("EC2-024", "EC2-026"):
        confidence += 15
        adjustments_applied.append(("compute_optimizer_ml", +15))
        notes.append("ML-validated by AWS Compute Optimizer")

    recommendation = finding.get("recommendation", "").lower()
    if is_iac_managed(details) and any(word in recommendation for word in ("delete", "terminate", "remove", "release", "deregister")):
        confidence += ADJUSTMENTS["iac_managed_delete"]
        adjustments_applied.append(("iac_managed_delete", ADJUSTMENTS["iac_managed_delete"]))
        notes.append("IaC-managed resource - validate change in source configuration before deletion")

    # 5. Data sufficiency check
    days_monitored = details.get("days_monitored") or details.get("evaluation_period_days")
    if days_monitored:
        if days_monitored < 7:
            confidence += ADJUSTMENTS["insufficient_data"]
            adjustments_applied.append(("insufficient_data", ADJUSTMENTS["insufficient_data"]))
            notes.append(f"Only {days_monitored} days of data - needs more monitoring")
        elif days_monitored >= 14:
            confidence += ADJUSTMENTS["consistent_pattern"]
            adjustments_applied.append(("consistent_pattern", ADJUSTMENTS["consistent_pattern"]))
            notes.append(f"Consistent pattern over {days_monitored} days")

    # 6. Lambda invocation check
    if check_id.startswith("LAMBDA"):
        invocations = details.get("invocations") or details.get("invocation_count", 0)
        if invocations < 50:
            confidence += ADJUSTMENTS["insufficient_data"]
            adjustments_applied.append(("insufficient_data", ADJUSTMENTS["insufficient_data"]))
            notes.append(f"Only {invocations} invocations - insufficient for analysis")

    # Clamp confidence to 0-100
    confidence = max(0, min(100, confidence))

    # Determine action based on final confidence.
    # Single scheme used across the whole project (CLAUDE.md, scan.md):
    #   >=70 approved, threshold..69 needs_validation, <threshold filtered
    if confidence >= 70:
        action = "approved"
    elif confidence >= threshold:
        action = "needs_validation"
    else:
        action = "filtered"

    return {
        "original_confidence": original_confidence,
        "final_confidence": confidence,
        "adjustments": adjustments_applied,
        "environment": env,
        "action": action,
        "notes": notes
    }


def review_findings(findings_path: str, profile: str, threshold: int = 50, force: bool = False) -> None:
    """Review all findings and update with review status."""

    # Load findings with error handling
    try:
        with open(findings_path, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {findings_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {findings_path}: {e}")
        sys.exit(1)

    findings = data.get("findings", [])

    if not findings:
        print("No findings to review.")
        print("Run a scan first to generate findings.json")
        return

    # Check if already reviewed
    already_reviewed = sum(1 for f in findings if f.get("review_status"))
    if already_reviewed == len(findings) and not force:
        print(f"All {len(findings)} findings already reviewed. Use --force to re-review.")
        return

    if force and already_reviewed > 0:
        print(f"Force re-reviewing {already_reviewed} previously reviewed findings...")

    print(f"Reviewing {len(findings)} findings...\n")

    # Statistics
    stats = {
        "approved": 0,
        "needs_validation": 0,
        "filtered": 0
    }

    reviewed_findings = []

    for finding in findings:
        # Skip already reviewed unless forced
        if finding.get("review_status") and not force:
            reviewed_findings.append(finding)
            action = finding["review_status"].get("action", "needs_validation")
            stats[action] = stats.get(action, 0) + 1
            continue

        # Clear previous review status if force re-reviewing
        if force and finding.get("review_status"):
            del finding["review_status"]

        # Analyze finding
        analysis = analyze_finding(finding, profile, threshold)

        # Add review status
        finding["review_status"] = {
            "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "original_confidence": analysis["original_confidence"],
            "final_confidence": analysis["final_confidence"],
            "adjustments": analysis["adjustments"],
            "environment": analysis["environment"],
            "action": analysis["action"],
            "notes": "; ".join(analysis["notes"]) if analysis["notes"] else "No adjustments"
        }

        stats[analysis["action"]] = stats.get(analysis["action"], 0) + 1
        reviewed_findings.append(finding)

    # Update findings
    data["findings"] = reviewed_findings

    # Update metadata
    if "metadata" not in data:
        data["metadata"] = {}

    data["metadata"]["review_summary"] = {
        "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_findings": len(findings),
        "approved": stats["approved"],
        "needs_validation": stats["needs_validation"],
        "filtered": stats["filtered"],
        "filter_threshold": threshold
    }

    # Calculate savings for approved findings only
    approved_savings = sum(
        f.get("monthly_savings", 0)
        for f in reviewed_findings
        if f.get("review_status", {}).get("action") == "approved"
    )
    data["metadata"]["approved_monthly_savings"] = round(approved_savings, 2)

    # Save updated findings
    with open(findings_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Print summary
    print("=" * 60)
    print("REVIEW SUMMARY")
    print("=" * 60)
    print(f"\nTotal Findings: {len(findings)}")
    print(f"\n✓ Approved (≥70%):              {stats['approved']}")
    print(f"⚠ Needs Validation ({threshold}-69%):    {stats['needs_validation']}")
    print(f"✗ Filtered (<{threshold}%):              {stats['filtered']}")
    print(f"\nApproved Monthly Savings: ${approved_savings:,.2f}")
    print(f"\nUpdated: {findings_path}")

    # Print filtered findings
    if stats["filtered"] > 0:
        print("\n" + "-" * 60)
        print("FILTERED FINDINGS (False Positives)")
        print("-" * 60)
        for f in reviewed_findings:
            if f.get("review_status", {}).get("action") == "filtered":
                print(f"\n• {f['check_id']}: {f.get('title', 'Unknown')}")
                print(f"  Resource: {f['resource_id'][:40]}...")
                print(f"  Original: {f['review_status']['original_confidence']}% → Final: {f['review_status']['final_confidence']}%")
                print(f"  Reason: {f['review_status']['notes']}")

    # Print needs validation
    if stats["needs_validation"] > 0:
        print("\n" + "-" * 60)
        print("NEEDS VALIDATION")
        print("-" * 60)
        for f in reviewed_findings:
            if f.get("review_status", {}).get("action") == "needs_validation":
                print(f"\n• {f['check_id']}: {f.get('title', 'Unknown')}")
                print(f"  Resource: {f['resource_id'][:40]}...")
                print(f"  Confidence: {f['review_status']['final_confidence']}%")
                print(f"  Issue: {f['review_status']['notes']}")


def main():
    parser = argparse.ArgumentParser(description="Review AWS cost optimization findings")
    parser.add_argument("findings_file", help="Path to findings.json")
    parser.add_argument("--profile", default="", help="AWS profile name")
    parser.add_argument("--threshold", type=int, default=50, help="Confidence below this is filtered out; 70+ is always approved (default: 50)")
    parser.add_argument("--force", action="store_true", help="Re-review already reviewed findings")

    args = parser.parse_args()

    review_findings(args.findings_file, args.profile, args.threshold, args.force)


if __name__ == "__main__":
    main()
