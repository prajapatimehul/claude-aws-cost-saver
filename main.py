#!/usr/bin/env python3
"""
AWS Cost Optimization System - Simplified CLI

Designed to work with Claude Code and AWS MCP for direct account scanning.

Usage:
    python main.py scan                    # Scan account using AWS MCP
    python main.py scan --from-cur <file>  # Analyze from CUR file
    python main.py report                  # Generate report from last scan
    python main.py spend-hotspots          # Rank scan focus using Cost Explorer
    python main.py checks                  # List all 180 checks
"""

import argparse
import json
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any


ALL_DOMAINS = [
    'compute', 'storage', 'database', 'networking',
    'serverless', 'reservations', 'containers',
    'advanced_databases', 'analytics', 'data_pipelines',
    'storage_advanced',
]


def load_checks() -> dict:
    """Load all check definitions from YAML (bundled inside the plugin)."""
    checks_path = (
        Path(__file__).parent / 'plugins' / 'aws-cost-saver' / 'checks' / 'all_checks.yaml'
    )
    with open(checks_path) as f:
        return yaml.safe_load(f)


def get_all_checks() -> list[dict]:
    """Get flat list of all checks across domains."""
    checks_data = load_checks()
    all_checks = []

    for domain in ALL_DOMAINS:
        if domain in checks_data:
            for check in checks_data[domain]:
                check['domain'] = domain
                all_checks.append(check)

    return all_checks


def cmd_checks(args):
    """List all checks."""
    checks = get_all_checks()

    print(f"AWS Cost Optimization Checks ({len(checks)} total)")
    print("=" * 70)

    if args.domain:
        checks = [c for c in checks if c['domain'] == args.domain]
        print(f"Domain: {args.domain}")
    else:
        print("All domains")

    print()

    current_domain = None
    for check in checks:
        if check['domain'] != current_domain:
            current_domain = check['domain']
            print(f"\n## {current_domain.upper()}")
            print("-" * 50)

        severity_icon = {
            'critical': '!!!',
            'high': '!! ',
            'medium': '!  ',
            'low': '   ',
            'info': 'i  '
        }.get(check.get('severity', 'info'), '   ')

        print(f"  {severity_icon} {check['id']}: {check['name']}")

    if args.verbose:
        print("\n\nDetailed Check Info (use --id <CHECK_ID> for specific check):")


def cmd_check_detail(args):
    """Show details for a specific check."""
    checks = get_all_checks()
    check = next((c for c in checks if c['id'] == args.id), None)

    if not check:
        print(f"Check not found: {args.id}")
        sys.exit(1)

    print(f"Check: {check['id']}")
    print("=" * 50)
    print(f"Name: {check['name']}")
    print(f"Domain: {check['domain']}")
    print(f"Severity: {check.get('severity', 'N/A')}")
    print(f"Category: {check.get('category', 'N/A')}")
    print(f"\nDescription:\n  {check.get('description', 'N/A')}")
    print(f"\nRecommendation:\n  {check.get('recommendation', 'N/A')}")

    if check.get('aws_cli'):
        print("\nAWS CLI Commands:")
        for cmd in check['aws_cli']:
            print(f"  - {cmd}")

    if check.get('thresholds'):
        print("\nThresholds:")
        for key, val in check['thresholds'].items():
            print(f"  {key}: {val}")


def cmd_scan_info(args):
    """Show scan information and required AWS CLI commands."""
    checks = get_all_checks()

    if args.domain:
        checks = [c for c in checks if c['domain'] == args.domain]

    # Collect unique AWS CLI commands
    commands = set()
    for check in checks:
        for cmd in check.get('aws_cli', []):
            # Extract base command (without placeholders)
            base_cmd = cmd.split('{')[0].strip()
            commands.add(base_cmd)

    print(f"AWS CLI Commands Required ({len(commands)} unique)")
    print("=" * 70)
    print("\nThese commands will be run via AWS MCP (mcp__awslabs-aws-api__call_aws)")
    print()

    for cmd in sorted(commands):
        print(f"  {cmd}")

    print("\n" + "=" * 70)
    print("To scan, Claude Code will run these commands across all enabled regions")
    print("and analyze the results against the check criteria.")


def cmd_report(args):
    """Generate markdown report from findings."""
    findings_path = Path(args.findings or 'findings.json')

    if not findings_path.exists():
        print(f"Findings file not found: {findings_path}")
        print("\nRun a scan first or specify findings file with --findings")
        sys.exit(1)

    with open(findings_path) as f:
        data = json.load(f)

    findings = data.get('findings', [])
    metadata = data.get('metadata', {})

    # Import and use markdown generator
    from src.outputs.markdown_report import generate_markdown_report

    output_path = Path(args.output or 'cost_optimization_report.md')
    generate_markdown_report(findings, metadata, output_path)

    print(f"Report generated: {output_path}")


def cmd_spend_hotspots(args):
    """Generate a spend-led hotspot summary from Cost Explorer."""
    from src.analysis.spend_hotspots import (
        build_spend_hotspots,
        format_spend_hotspots_markdown,
    )

    hotspot_data = build_spend_hotspots(
        profile=args.profile,
        top_services=args.top_services,
        usage_limit=args.usage_limit,
    )
    markdown = format_spend_hotspots_markdown(hotspot_data)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(markdown)
        print(f"Spend hotspot report generated: {output_path}")
        return

    print(markdown)


def cmd_init(args):
    """Initialize findings.json template."""
    template = {
        "metadata": {
            "account_id": "YOUR_ACCOUNT_ID",
            "regions": ["us-east-1"],
            "scan_date": datetime.now().isoformat(),
            "scan_type": "mcp"
        },
        "findings": []
    }

    output_path = Path(args.output or 'findings.json')
    with open(output_path, 'w') as f:
        json.dump(template, f, indent=2)

    print(f"Created: {output_path}")
    print("\nPopulate this file with findings from your AWS scan.")


def main():
    parser = argparse.ArgumentParser(
        description='AWS Cost Optimization - 180 checks across 11 domains',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # checks command
    checks_parser = subparsers.add_parser('checks', help='List all checks')
    checks_parser.add_argument('--domain', choices=ALL_DOMAINS, help='Filter by domain')
    checks_parser.add_argument('-v', '--verbose', action='store_true')

    # check command (single check detail)
    check_parser = subparsers.add_parser('check', help='Show check details')
    check_parser.add_argument('id', help='Check ID (e.g., EC2-001)')

    # scan-info command
    scan_parser = subparsers.add_parser('scan-info', help='Show scan requirements')
    scan_parser.add_argument('--domain', help='Filter by domain')

    # report command
    report_parser = subparsers.add_parser('report', help='Generate markdown report')
    report_parser.add_argument('--findings', help='Path to findings.json')
    report_parser.add_argument('--output', '-o', help='Output path for report')

    # spend-hotspots command
    hotspots_parser = subparsers.add_parser('spend-hotspots', help='Analyze last-month spend hotspots')
    hotspots_parser.add_argument('--profile', default='', help='AWS profile name')
    hotspots_parser.add_argument('--top-services', type=int, default=5, help='Number of top services to inspect')
    hotspots_parser.add_argument('--usage-limit', type=int, default=8, help='Usage types to show per service')
    hotspots_parser.add_argument('--output', '-o', help='Optional output Markdown path')

    # init command
    init_parser = subparsers.add_parser('init', help='Initialize findings template')
    init_parser.add_argument('--output', '-o', help='Output path')

    args = parser.parse_args()

    if args.command == 'checks':
        cmd_checks(args)
    elif args.command == 'check':
        cmd_check_detail(args)
    elif args.command == 'scan-info':
        cmd_scan_info(args)
    elif args.command == 'report':
        cmd_report(args)
    elif args.command == 'spend-hotspots':
        cmd_spend_hotspots(args)
    elif args.command == 'init':
        cmd_init(args)
    else:
        parser.print_help()
        print("\n" + "=" * 50)
        print("Quick Start:")
        print("  python main.py checks          # List all 180 checks")
        print("  python main.py check EC2-001   # View specific check")
        print("  python main.py scan-info       # Show AWS CLI commands needed")
        print("  python main.py spend-hotspots  # Rank scan focus from Cost Explorer")
        print("\nFor scanning, use Claude Code with AWS MCP to run the checks.")


if __name__ == '__main__':
    main()
