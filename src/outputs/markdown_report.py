"""
Markdown Report Generator for AWS Cost Optimization

Generates clean, readable Markdown reports from analysis findings.
"""

import json
from datetime import datetime
from pathlib import Path


def generate_markdown_report(
    findings: list[dict],
    metadata: dict,
    output_path: Path
) -> Path:
    """Generate a comprehensive Markdown report from analysis findings."""

    lines = []

    # Header
    lines.append("# AWS Cost Optimization Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Account:** {metadata.get('account_id', 'Unknown')}")
    lines.append(f"**Regions:** {', '.join(metadata.get('regions', ['Unknown']))}")
    lines.append("")

    # Executive Summary
    total_savings = sum(f.get('monthly_savings', 0) for f in findings)
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- **Total Findings:** {len(findings)}")
    lines.append(f"- **Estimated Monthly Savings:** ${total_savings:,.2f}")
    lines.append(f"- **Estimated Annual Savings:** ${total_savings * 12:,.2f}")
    lines.append("")

    # Severity Breakdown
    severity_counts = {}
    for f in findings:
        sev = f.get('severity', 'unknown')
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    lines.append("### Findings by Severity")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for sev in ['critical', 'high', 'medium', 'low', 'info']:
        if sev in severity_counts:
            lines.append(f"| {sev.upper()} | {severity_counts[sev]} |")
    lines.append("")

    # Domain Breakdown
    domain_savings = {}
    domain_counts = {}
    for f in findings:
        domain = f.get('domain', 'unknown')
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        domain_savings[domain] = domain_savings.get(domain, 0) + f.get('monthly_savings', 0)

    lines.append("### Findings by Domain")
    lines.append("")
    lines.append("| Domain | Findings | Monthly Savings |")
    lines.append("|--------|----------|-----------------|")
    for domain in sorted(domain_counts.keys()):
        lines.append(f"| {domain.title()} | {domain_counts[domain]} | ${domain_savings[domain]:,.2f} |")
    lines.append("")

    # Top 10 Recommendations
    sorted_findings = sorted(findings, key=lambda x: x.get('monthly_savings', 0), reverse=True)
    top_10 = sorted_findings[:10]

    if top_10:
        lines.append("## Top 10 Recommendations by Savings")
        lines.append("")
        for i, f in enumerate(top_10, 1):
            lines.append(f"### {i}. {f.get('check_id', 'N/A')}: {f.get('title', 'Untitled')}")
            lines.append("")
            lines.append(f"- **Resource:** `{f.get('resource_id', 'N/A')}`")
            lines.append(f"- **Monthly Savings:** ${f.get('monthly_savings', 0):,.2f}")
            lines.append(f"- **Severity:** {f.get('severity', 'N/A').upper()}")
            lines.append(f"- **Confidence:** {f.get('confidence', 0)}%")
            lines.append("")
            if f.get('description'):
                lines.append(f"**Issue:** {f.get('description')}")
                lines.append("")
            if f.get('recommendation'):
                lines.append(f"**Recommendation:** {f.get('recommendation')}")
                lines.append("")
            lines.append("---")
            lines.append("")

    # Detailed Findings by Domain
    lines.append("## All Findings by Domain")
    lines.append("")

    domains = sorted(set(f.get('domain', 'unknown') for f in findings))

    for domain in domains:
        domain_findings = [f for f in findings if f.get('domain') == domain]
        if not domain_findings:
            continue

        lines.append(f"### {domain.title()}")
        lines.append("")
        lines.append("| Check ID | Resource | Issue | Savings | Severity |")
        lines.append("|----------|----------|-------|---------|----------|")

        for f in sorted(domain_findings, key=lambda x: x.get('monthly_savings', 0), reverse=True):
            resource = f.get('resource_id', 'N/A')
            if len(resource) > 30:
                resource = resource[:27] + "..."
            title = f.get('title', 'N/A')
            if len(title) > 40:
                title = title[:37] + "..."
            lines.append(
                f"| {f.get('check_id', 'N/A')} | `{resource}` | {title} | "
                f"${f.get('monthly_savings', 0):,.2f} | {f.get('severity', 'N/A').upper()} |"
            )
        lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append("This report was generated using automated analysis of AWS resources.")
    lines.append("")
    lines.append("### Confidence Scoring")
    lines.append("")
    lines.append("- **90-100%**: Strong recommendation - 14+ days of data, no edge cases")
    lines.append("- **70-89%**: Likely correct - 7-14 days of data, minor edge cases")
    lines.append("- **50-69%**: Needs validation - Limited data or edge cases present")
    lines.append("- **<50%**: Excluded from report - Insufficient confidence")
    lines.append("")
    lines.append("### Edge Cases Considered")
    lines.append("")
    lines.append("- New resources (< 14 days old)")
    lines.append("- Recently modified resources")
    lines.append("- Auto Scaling Group members")
    lines.append("- Scheduled/burst workloads")
    lines.append("- Disaster recovery resources")
    lines.append("- Development/test environments")
    lines.append("")

    # Write file
    content = "\n".join(lines)
    output_path.write_text(content)

    return output_path


def generate_findings_detail_md(
    findings: list[dict],
    output_path: Path
) -> Path:
    """Generate detailed findings as a separate MD file."""

    lines = []
    lines.append("# Detailed Findings")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for f in findings:
        lines.append(f"## {f.get('check_id', 'N/A')}: {f.get('resource_id', 'N/A')}")
        lines.append("")
        lines.append(f"**Title:** {f.get('title', 'N/A')}")
        lines.append(f"**Domain:** {f.get('domain', 'N/A')}")
        lines.append(f"**Severity:** {f.get('severity', 'N/A').upper()}")
        lines.append(f"**Monthly Savings:** ${f.get('monthly_savings', 0):,.2f}")
        lines.append(f"**Confidence:** {f.get('confidence', 0)}%")
        lines.append("")

        if f.get('description'):
            lines.append("### Issue")
            lines.append(f"{f.get('description')}")
            lines.append("")

        if f.get('recommendation'):
            lines.append("### Recommendation")
            lines.append(f"{f.get('recommendation')}")
            lines.append("")

        if f.get('details'):
            lines.append("### Details")
            lines.append("```json")
            lines.append(json.dumps(f.get('details'), indent=2))
            lines.append("```")
            lines.append("")

        if f.get('edge_cases'):
            lines.append("### Edge Cases Detected")
            for ec in f.get('edge_cases', []):
                lines.append(f"- {ec}")
            lines.append("")

        lines.append("---")
        lines.append("")

    content = "\n".join(lines)
    output_path.write_text(content)

    return output_path
