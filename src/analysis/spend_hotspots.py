"""
Spend-led hotspot analysis using AWS Cost Explorer.

This is intended to run before a full inventory scan so the operator can focus
the scan on the services and usage types that are actually driving spend.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone


SERVICE_TO_DOMAINS = {
    "Amazon Elastic Compute Cloud - Compute": ["compute", "networking", "reservations"],
    "Amazon Simple Storage Service": ["storage", "networking"],
    "Amazon Relational Database Service": ["database", "reservations"],
    "AWS Lambda": ["serverless"],
    "Amazon Elastic Container Service": ["containers", "reservations"],
    "Amazon Elastic Kubernetes Service": ["containers"],
    "AmazonCloudWatch": ["storage"],
    "AWS CloudTrail": ["storage"],
    "Amazon OpenSearch Service": ["analytics"],
    "Amazon Redshift": ["advanced_databases"],
    "Amazon SageMaker": ["analytics", "reservations"],
    "AWS Glue": ["data_pipelines"],
}

USAGE_TYPE_PLAYBOOK = [
    {
        "matches": ("publicipv4", "idleaddress", "inuseaddress"),
        "title": "Public IPv4 charges",
        "checks": ["NET-018", "NET-001"],
        "recommendation": "Remove unnecessary public IPv4s, prefer private subnets, and use Public IP Insights when IPAM is enabled.",
    },
    {
        "matches": ("natgateway",),
        "title": "NAT Gateway processing",
        "checks": ["NET-016", "NET-007", "NET-002"],
        "recommendation": "Replace NAT-heavy S3 or DynamoDB traffic with gateway endpoints and review high-volume egress paths.",
    },
    {
        "matches": ("datatransfer-regional-bytes",),
        "title": "Cross-AZ data transfer",
        "checks": ["NET-004", "NET-017"],
        "recommendation": "Co-locate chatty resources in the same AZ and review replica or service mesh traffic patterns.",
    },
    {
        "matches": ("datatransfer-out-bytes", "aws-out-bytes"),
        "title": "Internet or inter-region egress",
        "checks": ["NET-005", "NET-006", "NET-008"],
        "recommendation": "Use CloudFront, cache more aggressively, and keep consumers closer to producers.",
    },
    {
        "matches": ("snapshot",),
        "title": "Snapshot storage",
        "checks": ["EC2-009", "RDS-007"],
        "recommendation": "Archive or delete stale snapshots instead of keeping them in the standard tier indefinitely.",
    },
    {
        "matches": ("eks-hours", "eks:cluster"),
        "title": "EKS control plane hours",
        "checks": ["EKS-001"],
        "recommendation": "Delete idle clusters and review old Kubernetes versions that may incur extended support charges.",
    },
]


def run_aws_command(command: str, profile: str) -> dict | None:
    """Execute an AWS CLI command and return parsed JSON."""
    full_command = f"{command} --output json"
    if profile:
        full_command += f" --profile {profile}"

    try:
        result = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def last_full_month_window(now: datetime | None = None) -> tuple[str, str]:
    """Return the Cost Explorer time window for the last full month."""
    now = now or datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_of_last_month = start_of_month - timedelta(days=1)
    start_of_last_month = end_of_last_month.replace(day=1)
    return (
        start_of_last_month.strftime("%Y-%m-%d"),
        start_of_month.strftime("%Y-%m-%d"),
    )


def _parse_grouped_cost_results(response: dict | None) -> list[dict]:
    """Parse Cost Explorer grouped results into a flat list."""
    if not response:
        return []

    results_by_time = response.get("ResultsByTime", [])
    if not results_by_time:
        return []

    groups = results_by_time[0].get("Groups", [])
    parsed_groups = []
    for group in groups:
        keys = group.get("Keys", [])
        amount = group.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", "0")
        try:
            parsed_groups.append(
                {
                    "key": keys[0] if keys else "Unknown",
                    "amount": round(float(amount), 2),
                }
            )
        except (TypeError, ValueError):
            continue
    return parsed_groups


def query_service_spend(profile: str) -> tuple[dict, list[dict]]:
    """Query Cost Explorer for last month's spend grouped by service."""
    start_date, end_date = last_full_month_window()
    command = (
        "aws ce get-cost-and-usage "
        f"--time-period Start={start_date},End={end_date} "
        "--granularity MONTHLY "
        "--metrics UnblendedCost "
        "--group-by Type=DIMENSION,Key=SERVICE"
    )
    response = run_aws_command(command, profile)
    services = sorted(
        _parse_grouped_cost_results(response),
        key=lambda item: item["amount"],
        reverse=True,
    )
    return {"start": start_date, "end": end_date}, services


def query_usage_type_spend(profile: str, service: str) -> list[dict]:
    """Query Cost Explorer for last month's spend grouped by usage type."""
    start_date, end_date = last_full_month_window()
    service_filter = json.dumps(
        {"Dimensions": {"Key": "SERVICE", "Values": [service]}},
        separators=(",", ":"),
    )
    command = (
        "aws ce get-cost-and-usage "
        f"--time-period Start={start_date},End={end_date} "
        "--granularity MONTHLY "
        "--metrics UnblendedCost "
        "--group-by Type=DIMENSION,Key=USAGE_TYPE "
        f"--filter '{service_filter}'"
    )
    response = run_aws_command(command, profile)
    return sorted(
        _parse_grouped_cost_results(response),
        key=lambda item: item["amount"],
        reverse=True,
    )


def _recommendations_for_usage_type(usage_type: str) -> list[dict]:
    """Map a usage type string to targeted scan recommendations."""
    normalized = usage_type.lower()
    recommendations = []
    for playbook in USAGE_TYPE_PLAYBOOK:
        if any(token in normalized for token in playbook["matches"]):
            recommendations.append(
                {
                    "title": playbook["title"],
                    "checks": playbook["checks"],
                    "recommendation": playbook["recommendation"],
                }
            )
    return recommendations


def build_spend_hotspots(
    profile: str,
    top_services: int = 5,
    usage_limit: int = 8,
) -> dict:
    """Build a spend-led hotspot summary."""
    period, services = query_service_spend(profile)

    hotspots = []
    focus_domains = []
    for service in services[:top_services]:
        service_name = service["key"]
        usage_types = query_usage_type_spend(profile, service_name)
        top_usage_types = usage_types[:usage_limit]
        playbooks = []
        for usage_type in top_usage_types:
            playbooks.extend(_recommendations_for_usage_type(usage_type["key"]))

        service_domains = SERVICE_TO_DOMAINS.get(service_name, [])
        focus_domains.extend(service_domains)
        hotspots.append(
            {
                "service": service_name,
                "monthly_spend": service["amount"],
                "domains": service_domains,
                "top_usage_types": top_usage_types,
                "playbooks": playbooks,
            }
        )

    unique_domains = []
    for domain in focus_domains:
        if domain not in unique_domains:
            unique_domains.append(domain)

    return {
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "profile": profile or "default",
        "focus_domains": unique_domains,
        "top_services": hotspots,
    }


def format_spend_hotspots_markdown(data: dict) -> str:
    """Render hotspot analysis as Markdown."""
    period = data.get("period", {})
    top_services = data.get("top_services", [])
    focus_domains = data.get("focus_domains", [])

    lines = [
        "# AWS Spend Hotspots",
        "",
        f"**Period:** {period.get('start', 'Unknown')} to {period.get('end', 'Unknown')}",
        f"**Generated:** {data.get('generated_at', 'Unknown')}",
        f"**AWS Profile:** {data.get('profile', 'default')}",
        "",
    ]

    if focus_domains:
        lines.append(f"**Recommended scan order:** {', '.join(focus_domains)}")
        lines.append("")

    if not top_services:
        lines.append("No Cost Explorer data was returned.")
        return "\n".join(lines)

    lines.extend(
        [
            "## Top Services",
            "",
            "| Service | Monthly Spend | Focus Domains |",
            "|---------|---------------:|---------------|",
        ]
    )
    for service in top_services:
        domains = ", ".join(service.get("domains", [])) or "manual review"
        lines.append(
            f"| {service['service']} | ${service['monthly_spend']:,.2f} | {domains} |"
        )
    lines.append("")

    for service in top_services:
        lines.append(f"## {service['service']}")
        lines.append("")
        lines.append(f"Estimated monthly spend: **${service['monthly_spend']:,.2f}**")
        lines.append("")

        usage_types = service.get("top_usage_types", [])
        if usage_types:
            lines.append("| Usage Type | Monthly Spend |")
            lines.append("|------------|---------------:|")
            for usage_type in usage_types:
                lines.append(
                    f"| {usage_type['key']} | ${usage_type['amount']:,.2f} |"
                )
            lines.append("")

        playbooks = service.get("playbooks", [])
        if playbooks:
            lines.append("Targeted actions:")
            seen = set()
            for playbook in playbooks:
                key = (playbook["title"], tuple(playbook["checks"]))
                if key in seen:
                    continue
                seen.add(key)
                checks = ", ".join(playbook["checks"])
                lines.append(
                    f"- {playbook['title']} -> {checks}. {playbook['recommendation']}"
                )
            lines.append("")

    return "\n".join(lines)
