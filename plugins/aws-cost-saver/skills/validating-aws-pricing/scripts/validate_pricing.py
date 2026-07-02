#!/usr/bin/env python3
"""
AWS Pricing Validator & Corrector

Validates cost optimization findings >$100 with real AWS Pricing API data.
Smaller findings must already carry compliant pricing metadata, resolve from
the verified table, or fall back to pricing_unknown (never estimated).

Usage:
    python validate_pricing.py findings.json --profile your-profile
    python validate_pricing.py findings.json --profile your-profile --threshold 50
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


# Average hours per month (365 * 24 / 12)
HOURS_PER_MONTH = 730

# AWS Pricing API region (only available in us-east-1 and ap-south-1)
PRICING_REGION = "us-east-1"

# Minimum savings to trigger real API validation (default $100)
DEFAULT_VALIDATION_THRESHOLD = 100

ALLOWED_PRICING_SOURCES = {
    "aws_pricing_api",
    "verified_table",
    "aws_cost_explorer",
    "pricing_unknown",
}

# Verified table entries are only used where the repository explicitly allows
# exact table pricing as a fallback to the Pricing API.
VERIFIED_PRICING = {
    "global": {
        "network:public_ipv4_hour": 0.005,
        "secrets:monthly": 0.40,
        "route53:hosted_zone_monthly": 0.50,
    },
    "us-east-1": {
        "ebs:gp3": 0.08,
        "ebs:gp2": 0.10,
        "ebs:io2": 0.125,
        "ebs:snapshot_standard": 0.05,
        "ebs:snapshot_archive": 0.0125,
        "cloudwatch:logs-storage": 0.03,
        "rds:snapshot": 0.095,
        "ecr:storage": 0.10,
    },
}

# Explicit check ID to calculator mapping (fixes routing bug)
# Each check ID is explicitly mapped to avoid prefix-matching errors
# Comments match the check names in checks/all_checks.yaml
CHECK_ID_ROUTING = {
    # EC2 instance checks
    "EC2-001": "ec2",      # Idle Instances -> full monthly cost
    "EC2-002": "ec2",      # Over-provisioned Instances -> rightsizing delta
    "EC2-003": "ec2",      # Previous Generation Instances -> rightsizing delta
    "EC2-004": "ec2",      # RI/Savings Plan Candidate -> pricing_unknown (needs CE)
    "EC2-005": "ec2",      # Stopped Instances with EBS -> pricing_unknown (storage-driven)
    "EC2-007": "ec2",      # Spot Instance Opportunity -> pricing_unknown (needs Spot pricing)

    # EBS checks
    "EC2-006": "ebs",      # GP2 to GP3 Migration
    "EC2-009": "ebs",      # Old EBS Snapshots
    "EC2-011": "ebs",      # Over-provisioned EBS IOPS
    "EC2-012": "ebs",      # Unattached EBS Volumes
    "EC2-013": "ebs",      # Oversized EBS Volumes
    "EBS-001": "ebs",      # Unattached Volumes (duplicate of EC2-012)
    "EBS-002": "ebs",      # GP2 to GP3 Migration (duplicate of EC2-006)
    "EBS-003": "ebs",      # Over-provisioned IOPS
    "EBS-004": "ebs",      # Orphaned Snapshots
    "EBS-005": "ebs",      # Oversized Volumes
    "EBS-006": "ebs",      # Throughput Optimization

    # RDS checks
    "RDS-001": "rds",      # Idle Databases -> full monthly cost
    "RDS-002": "rds",      # Over-provisioned Instances -> rightsizing delta
    "RDS-003": "rds",      # Single-AZ in Production (info; usually no savings)
    "RDS-004": "rds",      # Previous Generation Types -> rightsizing delta
    "RDS-005": "rds",      # No RI Coverage -> pricing_unknown (needs CE)
    "RDS-006": "rds",      # Excessive Storage
    "RDS-007": "rds",      # Old Snapshots
    "RDS-008": "rds",      # Multi-AZ for Non-Production

    # ElastiCache checks
    "CACHE-001": "elasticache",  # Oversized ElastiCache
    "CACHE-002": "elasticache",  # Reserved Node Opportunity
    "CACHE-003": "elasticache",  # Unused Clusters

    # CloudWatch/CloudTrail checks
    "LOG-001": "cloudwatch",   # CloudWatch Logs Retention
    "LOG-002": "cloudwatch",   # CloudTrail Duplication
    "CT-001": "cloudwatch",    # CloudTrail Data Event Costs

    # Lambda checks
    "LAMBDA-001": "lambda",    # Memory Over-provisioning
    "LAMBDA-002": "lambda",    # Timeout Optimization
    "LAMBDA-003": "lambda",    # Provisioned Concurrency Review
    "LAMBDA-004": "lambda",    # Unused Functions
    "LAMBDA-005": "lambda",    # ARM64 Migration Opportunity

    # S3 checks
    "S3-001": "s3",           # No Lifecycle Policy
    "S3-002": "s3",           # Standard to IA Opportunity
    "S3-003": "s3",           # IA to Glacier Opportunity
    "S3-004": "s3",           # Incomplete Multipart Uploads
    "S3-005": "s3",           # Excessive Versioning

    # Networking checks
    "NET-001": "network",     # Unused Elastic IPs
    "NET-002": "network",     # NAT Gateway Optimization
    "NET-003": "network",     # Idle Load Balancers
    "NET-004": "network",     # Cross-AZ Data Transfer
    "NET-016": "network",     # NAT Gateway Data Processing Cost
    "NET-017": "network",     # Data Transfer Cost Breakdown
    "NET-018": "network",     # Public IPv4 Address Charges
    "R53-001": "network",     # Unused Route 53 Hosted Zones

    # Other service checks
    "SECRETS-001": "misc",    # Unused Secrets Manager Secrets
    "ECR-001": "misc",        # ECR Image Lifecycle

    # Compute Optimizer checks
    "EC2-024": "ec2",         # Compute Optimizer ML Rightsizing -> rightsizing delta
    "EC2-026": "ec2",         # Compute Optimizer Idle Detection -> full monthly cost
    "EC2-027": "ec2",         # Memory Utilization Check -> rightsizing delta

    # Reservation purchase recommendations (use AWS-provided savings)
    "RI-007": "reservations", # RI Purchase Recommendation
    "SP-005": "reservations", # SP Purchase Recommendation
}

# Map service domain to Cost Explorer service name
DOMAIN_TO_CE_SERVICE = {
    "ec2": "Amazon Elastic Compute Cloud - Compute",
    "ebs": "Amazon Elastic Compute Cloud - Compute",  # EBS is part of EC2 billing
    "rds": "Amazon Relational Database Service",
    "elasticache": "Amazon ElastiCache",
    "cloudwatch": "AmazonCloudWatch",
    "lambda": "AWS Lambda",
    "s3": "Amazon Simple Storage Service",
    "network": "Amazon Elastic Compute Cloud - Compute",
    "misc": None,          # Mixed services - skip CE sanity check
    "reservations": None,  # AWS-provided recommendations - trust the savings estimate
}


def run_aws_command(command: str, profile: str) -> dict | None:
    """Execute AWS CLI command and return JSON response."""
    full_command = f"{command} --output json"
    if profile:
        full_command += f" --profile {profile}"

    try:
        result = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def get_verified_price(key: str, region: str | None = None) -> float | None:
    """Get an exact verified-table price when one exists."""
    if key in VERIFIED_PRICING["global"]:
        return VERIFIED_PRICING["global"][key]
    if region and region in VERIFIED_PRICING and key in VERIFIED_PRICING[region]:
        return VERIFIED_PRICING[region][key]
    return None


def ensure_details(finding: dict) -> dict:
    """Return the mutable details object."""
    return finding.setdefault("details", {})


def pricing_unknown(reason: str, **extra: object) -> tuple[float, dict]:
    """Return a compliant pricing-unknown payload."""
    metadata = {
        "source": "pricing_unknown",
        "pricing_unknown": True,
        "reason": reason,
    }
    metadata.update(extra)
    return 0.0, metadata


def existing_pricing_is_valid(finding: dict) -> bool:
    """Check whether a finding already has compliant pricing metadata."""
    details = finding.get("details", {})
    source = details.get("pricing_source")
    if source not in ALLOWED_PRICING_SOURCES:
        return False

    if source == "pricing_unknown":
        return finding.get("monthly_savings", 0) == 0

    if source == "aws_cost_explorer":
        return finding.get("monthly_savings", 0) >= 0

    return bool(details.get("calculation"))


def query_ec2_pricing(instance_type: str, profile: str, region: str = "us-east-1") -> float | None:
    """Query AWS Pricing API for EC2 instance hourly rate."""
    # Map region code to location name
    location_map = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "eu-west-1": "EU (Ireland)",
        "eu-central-1": "EU (Frankfurt)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
    }
    location = location_map.get(region)
    if not location:
        return None

    cmd = f'''aws pricing get-products --region {PRICING_REGION} \
        --service-code AmazonEC2 \
        --filters \
        "Type=TERM_MATCH,Field=instanceType,Value={instance_type}" \
        "Type=TERM_MATCH,Field=location,Value={location}" \
        "Type=TERM_MATCH,Field=operatingSystem,Value=Linux" \
        "Type=TERM_MATCH,Field=tenancy,Value=Shared" \
        "Type=TERM_MATCH,Field=preInstalledSw,Value=NA" \
        "Type=TERM_MATCH,Field=capacitystatus,Value=Used" \
        --max-results 1'''

    result = run_aws_command(cmd, profile)
    return parse_pricing_response(result)


def query_rds_pricing(instance_type: str, engine: str, profile: str, region: str = "us-east-1") -> float | None:
    """Query AWS Pricing API for RDS instance hourly rate."""
    location_map = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-2": "US West (Oregon)",
        "eu-west-1": "EU (Ireland)",
        "eu-central-1": "EU (Frankfurt)",
    }
    location = location_map.get(region)
    if not location:
        return None

    # Normalize engine name
    engine_map = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "aurora-postgresql": "Aurora PostgreSQL",
        "aurora-mysql": "Aurora MySQL",
    }
    db_engine = engine_map.get(engine.lower())
    if not db_engine:
        return None

    cmd = f'''aws pricing get-products --region {PRICING_REGION} \
        --service-code AmazonRDS \
        --filters \
        "Type=TERM_MATCH,Field=instanceType,Value={instance_type}" \
        "Type=TERM_MATCH,Field=location,Value={location}" \
        "Type=TERM_MATCH,Field=databaseEngine,Value={db_engine}" \
        "Type=TERM_MATCH,Field=deploymentOption,Value=Single-AZ" \
        --max-results 1'''

    result = run_aws_command(cmd, profile)
    return parse_pricing_response(result)


def query_ebs_pricing(volume_type: str, profile: str, region: str = "us-east-1") -> float | None:
    """Query AWS Pricing API for EBS volume price per GB-month."""
    location_map = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-2": "US West (Oregon)",
    }
    location = location_map.get(region)
    if not location:
        return None

    cmd = f'''aws pricing get-products --region {PRICING_REGION} \
        --service-code AmazonEC2 \
        --filters \
        "Type=TERM_MATCH,Field=volumeApiName,Value={volume_type}" \
        "Type=TERM_MATCH,Field=location,Value={location}" \
        --max-results 5'''

    result = run_aws_command(cmd, profile)
    return parse_pricing_response(result)


def parse_pricing_response(response: dict | None) -> float | None:
    """Extract price from AWS Pricing API response."""
    if not response:
        return None

    price_list = response.get('PriceList', [])
    if not price_list:
        return None

    try:
        product_data = json.loads(price_list[0])
        on_demand = product_data.get('terms', {}).get('OnDemand', {})

        for sku_term in on_demand.values():
            price_dimensions = sku_term.get('priceDimensions', {})
            for dimension in price_dimensions.values():
                price_per_unit = dimension.get('pricePerUnit', {})
                usd_price = price_per_unit.get('USD')
                if usd_price:
                    return float(usd_price)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None

    return None


def get_service_monthly_spend(service_domain: str, profile: str) -> float | None:
    """Query Cost Explorer for actual monthly spend on a service.

    This is the MANDATORY sanity check: findings cannot save more than service costs.
    """
    ce_service = DOMAIN_TO_CE_SERVICE.get(service_domain)
    if not ce_service:
        return None

    # Get last month's actual spend
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1)
    end_of_last_month = start_of_month - timedelta(days=1)
    start_of_last_month = end_of_last_month.replace(day=1)

    start_date = start_of_last_month.strftime("%Y-%m-%d")
    end_date = start_of_month.strftime("%Y-%m-%d")

    cmd = f'''aws ce get-cost-and-usage \
        --time-period Start={start_date},End={end_date} \
        --granularity MONTHLY \
        --metrics UnblendedCost \
        --filter '{{"Dimensions": {{"Key": "SERVICE", "Values": ["{ce_service}"]}}}}' '''

    result = run_aws_command(cmd, profile)
    if not result:
        return None

    try:
        results_by_time = result.get("ResultsByTime", [])
        if results_by_time:
            total = results_by_time[0].get("Total", {})
            cost = total.get("UnblendedCost", {}).get("Amount")
            if cost:
                return float(cost)
    except (KeyError, ValueError, TypeError):
        pass

    return None


def sanity_check_finding(finding: dict, profile: str, service_spend_cache: dict) -> dict:
    """MANDATORY: Validate that finding savings don't exceed service spend.

    Per CLAUDE.md: finding.monthly_savings <= service.monthly_spend
    If validation fails, cap savings and mark as corrected.
    """
    check_id = finding.get("check_id", "")
    monthly_savings = finding.get("monthly_savings", 0)

    # Get service domain from check ID routing
    service_domain = CHECK_ID_ROUTING.get(check_id)
    if not service_domain:
        return finding  # Unknown check, skip sanity check

    # Cache service spend queries (expensive API call)
    if service_domain not in service_spend_cache:
        print(f"  Querying Cost Explorer for {service_domain} spend...")
        service_spend_cache[service_domain] = get_service_monthly_spend(service_domain, profile)

    service_spend = service_spend_cache.get(service_domain)

    # SANITY CHECK: savings cannot exceed service spend
    if service_spend is not None and monthly_savings > service_spend:
        original_savings = monthly_savings
        capped_savings = round(service_spend, 2)

        finding["monthly_savings"] = capped_savings
        finding["pricing_corrected"] = True
        details = ensure_details(finding)
        details["pricing_corrected"] = True
        finding["sanity_check"] = {
            "original_savings": original_savings,
            "service_spend": round(service_spend, 2),
            "capped_to": capped_savings,
            "reason": f"Savings ${original_savings:.2f} exceeded service spend ${service_spend:.2f}"
        }
        print(f"  ⚠️  {check_id}: Capped ${original_savings:.2f} -> ${capped_savings:.2f} (service spend: ${service_spend:.2f})")

    return finding


def calculate_ebs_storage_cost(details: dict, region: str) -> tuple[float | None, dict]:
    """Calculate exact EBS storage cost when verified-table inputs are sufficient."""
    size_gb = details.get("size_gb") or details.get("snapshot_size_gb") or details.get("storage_gb")
    volume_type = (details.get("volume_type") or "").lower()
    if not size_gb or not volume_type:
        return None, {}

    if volume_type == "gp3":
        gp3_rate = get_verified_price("ebs:gp3", region)
        if gp3_rate is None:
            return None, {}
        iops = max(0, int(float(details.get("iops", 3000))) - 3000)
        throughput = max(
            0,
            int(float(details.get("throughput", details.get("throughput_mbps", 125)))) - 125,
        )
        monthly_cost = (float(size_gb) * gp3_rate) + (iops * 0.005) + (throughput * 0.04)
        return round(monthly_cost, 2), {
            "price_per_gb": gp3_rate,
            "iops_above_baseline": iops,
            "throughput_above_baseline": throughput,
        }

    if volume_type in {"gp2", "io2"}:
        price_per_gb = get_verified_price(f"ebs:{volume_type}", region)
        if price_per_gb is None:
            return None, {}
        return round(float(size_gb) * price_per_gb, 2), {
            "price_per_gb": price_per_gb,
        }

    return None, {}


def calculate_ec2_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for EC2 findings."""
    details = ensure_details(finding)
    check_id = finding.get("check_id", "")
    instance_type = details.get("instance_type", "")
    region = details.get("region", "")

    if check_id in {"EC2-004", "EC2-007"}:
        return pricing_unknown("Savings require AWS Cost Explorer or Spot-specific pricing inputs.")

    if check_id == "EC2-005":
        return pricing_unknown("Stopped-instance savings depend on attached storage details, not the instance alone.")

    if not use_api or not instance_type or not region:
        return pricing_unknown("EC2 pricing requires AWS Pricing API with instance_type and region.")

    print(f"  Querying EC2 pricing for {instance_type}...")
    current_rate = query_ec2_pricing(instance_type, profile, region)
    if current_rate is None:
        return pricing_unknown("AWS Pricing API did not return EC2 pricing.", instance_type=instance_type, region=region)

    current_monthly = current_rate * HOURS_PER_MONTH

    if check_id in {"EC2-001", "EC2-026"}:
        return round(current_monthly, 2), {
            "source": "aws_pricing_api",
            "hourly_rate": current_rate,
            "calculation": f"{current_rate} * {HOURS_PER_MONTH} hours",
        }

    recommended_instance_type = details.get("recommended_instance_type")
    if not recommended_instance_type:
        return pricing_unknown("No exact recommended_instance_type was supplied for EC2 rightsizing.")

    print(f"  Querying EC2 pricing for recommended target {recommended_instance_type}...")
    recommended_rate = query_ec2_pricing(recommended_instance_type, profile, region)
    if recommended_rate is None:
        return pricing_unknown(
            "AWS Pricing API did not return pricing for the recommended EC2 target.",
            instance_type=instance_type,
            recommended_instance_type=recommended_instance_type,
            region=region,
        )

    savings = max(0.0, (current_rate - recommended_rate) * HOURS_PER_MONTH)
    return round(savings, 2), {
        "source": "aws_pricing_api",
        "current_hourly_rate": current_rate,
        "recommended_hourly_rate": recommended_rate,
        "recommended_instance_type": recommended_instance_type,
        "calculation": (
            f"({current_rate} - {recommended_rate}) * {HOURS_PER_MONTH} hours"
        ),
    }


def calculate_ebs_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for EBS findings."""
    details = ensure_details(finding)
    check_id = finding.get("check_id", "")
    region = details.get("region", "")
    storage_cost, cost_metadata = calculate_ebs_storage_cost(details, region)

    if check_id in {"EC2-012", "EBS-001"}:
        if storage_cost is None:
            return pricing_unknown("Exact EBS volume pricing requires size_gb, volume_type, and a supported region.")
        size_gb = details.get("size_gb")
        return storage_cost, {
            "source": "verified_table",
            **cost_metadata,
            "size_gb": size_gb,
            "calculation": f"{size_gb} GB storage cost from verified us-east-1 pricing table",
        }

    if check_id in {"EC2-006", "EBS-002"}:
        size_gb = details.get("size_gb")
        if not size_gb or region != "us-east-1":
            return pricing_unknown("Exact gp2 to gp3 savings are only supported from the verified us-east-1 table.")

        current_details = {**details, "volume_type": "gp2"}
        target_details = {
            **details,
            "volume_type": "gp3",
            "iops": details.get("target_iops", details.get("iops", 3000)),
            "throughput": details.get("target_throughput", details.get("throughput", 125)),
        }
        current_cost, current_meta = calculate_ebs_storage_cost(current_details, region)
        target_cost, target_meta = calculate_ebs_storage_cost(target_details, region)
        if current_cost is None or target_cost is None:
            return pricing_unknown("Could not derive an exact gp2 to gp3 comparison.")

        savings = max(0.0, current_cost - target_cost)
        return round(savings, 2), {
            "source": "verified_table",
            "current_monthly": current_cost,
            "target_monthly": target_cost,
            "current_price_per_gb": current_meta.get("price_per_gb"),
            "target_price_per_gb": target_meta.get("price_per_gb"),
            "calculation": f"{current_cost} - {target_cost}",
        }

    if check_id in {"EC2-009", "EBS-004"}:
        snapshot_gb = details.get("snapshot_size_gb") or details.get("size_gb")
        if details.get("archive_candidate"):
            snapshot_price = get_verified_price("ebs:snapshot_standard", region)
            archive_price = get_verified_price("ebs:snapshot_archive", region)
            if not snapshot_gb or snapshot_price is None or archive_price is None:
                return pricing_unknown("Snapshot archive savings require snapshot size and a verified-table region.")
            savings = float(snapshot_gb) * (snapshot_price - archive_price)
            return round(savings, 2), {
                "source": "verified_table",
                "snapshot_size_gb": snapshot_gb,
                "standard_price_per_gb": snapshot_price,
                "archive_price_per_gb": archive_price,
                "calculation": f"{snapshot_gb} GB * (${snapshot_price} - ${archive_price})",
            }

        snapshot_price = get_verified_price("ebs:snapshot_standard", region)
        if not snapshot_gb or snapshot_price is None:
            return pricing_unknown("Snapshot savings require snapshot size and a verified-table region.")
        savings = float(snapshot_gb) * snapshot_price
        return round(savings, 2), {
            "source": "verified_table",
            "snapshot_size_gb": snapshot_gb,
            "price_per_gb": snapshot_price,
            "calculation": f"{snapshot_gb} GB * ${snapshot_price}/GB-month",
        }

    return pricing_unknown("No exact pricing formula is implemented for this EBS finding type.")


def calculate_rds_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for RDS findings."""
    details = ensure_details(finding)
    check_id = finding.get("check_id", "")
    instance_type = details.get("instance_type", "")
    engine = details.get("engine", "")
    region = details.get("region", "")

    if check_id == "RDS-005":
        return pricing_unknown("Per-instance RI savings require Cost Explorer reservation recommendations.")

    if check_id == "RDS-007":
        storage_gb = details.get("allocated_storage_gb") or details.get("snapshot_size_gb")
        snapshot_price = get_verified_price("rds:snapshot", region)
        if not storage_gb or snapshot_price is None:
            return pricing_unknown("RDS snapshot savings require storage size and a verified-table region.")
        savings = float(storage_gb) * snapshot_price
        return round(savings, 2), {
            "source": "verified_table",
            "storage_gb": storage_gb,
            "price_per_gb": snapshot_price,
            "calculation": f"{storage_gb} GB * ${snapshot_price}/GB-month",
        }

    if not use_api or not instance_type or not engine or not region:
        return pricing_unknown("RDS pricing requires AWS Pricing API with instance_type, engine, and region.")

    print(f"  Querying RDS pricing for {instance_type} ({engine})...")
    current_rate = query_rds_pricing(instance_type, engine, profile, region)
    if current_rate is None:
        return pricing_unknown("AWS Pricing API did not return RDS pricing.", instance_type=instance_type, engine=engine, region=region)

    current_monthly = current_rate * HOURS_PER_MONTH
    if check_id == "RDS-001":
        return round(current_monthly, 2), {
            "source": "aws_pricing_api",
            "hourly_rate": current_rate,
            "calculation": f"{current_rate} * {HOURS_PER_MONTH} hours",
        }

    recommended_instance_type = details.get("recommended_instance_type")
    if not recommended_instance_type:
        return pricing_unknown("No exact recommended_instance_type was supplied for RDS rightsizing.")

    print(f"  Querying RDS pricing for recommended target {recommended_instance_type} ({engine})...")
    recommended_rate = query_rds_pricing(recommended_instance_type, engine, profile, region)
    if recommended_rate is None:
        return pricing_unknown(
            "AWS Pricing API did not return pricing for the recommended RDS target.",
            instance_type=instance_type,
            recommended_instance_type=recommended_instance_type,
            engine=engine,
            region=region,
        )

    savings = max(0.0, (current_rate - recommended_rate) * HOURS_PER_MONTH)
    return round(savings, 2), {
        "source": "aws_pricing_api",
        "current_hourly_rate": current_rate,
        "recommended_hourly_rate": recommended_rate,
        "recommended_instance_type": recommended_instance_type,
        "calculation": (
            f"({current_rate} - {recommended_rate}) * {HOURS_PER_MONTH} hours"
        ),
    }


def calculate_elasticache_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for ElastiCache findings."""
    return pricing_unknown("ElastiCache savings require an exact target cost model or pre-validated pricing.")


def calculate_cloudwatch_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for CloudWatch findings."""
    details = ensure_details(finding)
    stored_gb = details.get("stored_gb", 0)
    region = details.get("region", "")
    storage_price = get_verified_price("cloudwatch:logs-storage", region)

    if not stored_gb or storage_price is None:
        return pricing_unknown("CloudWatch Logs storage savings require stored_gb and a verified-table region.")

    savings = float(stored_gb) * storage_price
    return round(savings, 2), {
        "source": "verified_table",
        "stored_gb": stored_gb,
        "price_per_gb": storage_price,
        "calculation": f"{stored_gb:.1f} GB * ${storage_price}/GB-month",
    }


def calculate_lambda_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for Lambda findings."""
    return pricing_unknown("Lambda savings require invocation or duration-level pricing inputs.")


def calculate_s3_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for S3 findings."""
    return pricing_unknown("S3 lifecycle savings require exact object-tier breakdowns or pre-validated pricing.")


def calculate_network_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for networking findings (EIPs, NAT, etc.)."""
    check_id = finding.get("check_id", "")
    details = ensure_details(finding)
    hourly_rate = get_verified_price("network:public_ipv4_hour")

    if check_id in {"NET-001", "NET-018"}:
        count = float(
            (
            details.get("public_ipv4_count")
            or details.get("elastic_ip_count")
            or details.get("count")
            or 1
            )
        )
        savings = count * hourly_rate * HOURS_PER_MONTH
        return round(savings, 2), {
            "source": "verified_table",
            "hourly_rate": hourly_rate,
            "address_count": count,
            "calculation": f"{count} * {hourly_rate} * {HOURS_PER_MONTH} hours",
        }

    if check_id == "R53-001":
        count = float(details.get("zone_count") or details.get("count") or 1)
        monthly_rate = get_verified_price("route53:hosted_zone_monthly")
        savings = count * monthly_rate
        return round(savings, 2), {
            "source": "verified_table",
            "zone_count": count,
            "price_per_zone": monthly_rate,
            "calculation": f"{count} * ${monthly_rate}/hosted-zone-month",
        }

    return pricing_unknown("Network transfer findings require exact Cost Explorer usage-type inputs.")


def calculate_misc_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for misc service findings (Secrets Manager, ECR, etc.)."""
    check_id = finding.get("check_id", "")
    details = ensure_details(finding)

    if check_id == "SECRETS-001":
        count = float(details.get("secret_count", 1))
        monthly_rate = get_verified_price("secrets:monthly")
        savings = count * monthly_rate
        return round(savings, 2), {
            "source": "verified_table",
            "calculation": f"{count} secrets * ${monthly_rate}/secret-month",
        }

    if check_id == "ECR-001":
        region = details.get("region", "")
        storage_gb = details.get("untagged_image_size_gb", 0)
        storage_rate = get_verified_price("ecr:storage", region)
        if not storage_gb or storage_rate is None:
            return pricing_unknown("ECR savings require image storage size and a verified-table region.")
        savings = float(storage_gb) * storage_rate
        return round(savings, 2), {
            "source": "verified_table",
            "calculation": f"{storage_gb} GB * ${storage_rate}/GB-month",
        }

    return pricing_unknown("No exact pricing formula is implemented for this finding type.")


def calculate_reservation_savings(finding: dict, profile: str, use_api: bool = False) -> tuple[float, dict]:
    """Calculate savings for reservation purchase recommendations.

    These come from AWS CE APIs which already provide savings estimates.
    Trust the AWS-provided number.
    """
    return finding.get("monthly_savings", 0), {
        "source": "aws_cost_explorer",
        "note": "Savings estimate provided by AWS, not calculated locally"
    }


# Calculator dispatch table (maps service domain to calculator function)
CALCULATOR_DISPATCH = {
    "ec2": calculate_ec2_savings,
    "ebs": calculate_ebs_savings,
    "rds": calculate_rds_savings,
    "elasticache": calculate_elasticache_savings,
    "cloudwatch": calculate_cloudwatch_savings,
    "lambda": calculate_lambda_savings,
    "s3": calculate_s3_savings,
    "network": calculate_network_savings,
    "misc": calculate_misc_savings,
    "reservations": calculate_reservation_savings,
}


def correct_finding(finding: dict, profile: str, threshold: float = 100) -> dict:
    """Correct a single finding with accurate pricing.

    Only queries AWS Pricing API for findings with savings > threshold.
    Uses explicit check ID routing (not prefix matching) to avoid misrouting bugs.
    """
    check_id = finding.get("check_id", "")
    original_savings = finding.get("monthly_savings", 0)
    details = ensure_details(finding)

    if existing_pricing_is_valid(finding):
        metadata = {
            "source": details.get("pricing_source"),
            "calculation": details.get("calculation"),
            "preserved_existing_pricing": True,
        }
        finding["pricing_validated"] = {
            **metadata,
            "original_estimate": original_savings,
            "api_validated": details.get("pricing_source") == "aws_pricing_api",
            "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return finding

    # Only use real API for big findings (> threshold)
    use_api = original_savings > threshold

    # Route to appropriate calculator using EXPLICIT check ID mapping
    # This fixes the bug where startswith("EC2-00") matched EC2-001 through EC2-009
    service_domain = CHECK_ID_ROUTING.get(check_id)

    if service_domain and service_domain in CALCULATOR_DISPATCH:
        calculator = CALCULATOR_DISPATCH[service_domain]
        savings, metadata = calculator(finding, profile, use_api)
    else:
        savings, metadata = pricing_unknown(f"Unknown check_id: {check_id}")

    # Update finding
    pricing_source = metadata.pop("source")
    finding["monthly_savings"] = round(savings, 2)
    details["pricing_source"] = pricing_source
    if pricing_source == "pricing_unknown":
        details["pricing_unknown"] = True
        details.pop("calculation", None)
    elif metadata.get("calculation"):
        details["calculation"] = metadata["calculation"]

    finding["pricing_validated"] = {
        **metadata,
        "source": pricing_source,
        "original_estimate": original_savings,
        "api_validated": pricing_source == "aws_pricing_api",
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }

    return finding


def correct_findings(findings_path: str, profile: str, threshold: float = 100) -> dict:
    """Correct all findings with accurate pricing.

    Only queries AWS Pricing API for findings with savings > threshold.
    Smaller findings must already be compliant, use a verified table, or fall back
    to pricing_unknown.

    MANDATORY: Also performs sanity check (savings <= service spend).
    """
    try:
        with open(findings_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {findings_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    findings = data.get("findings", [])
    metadata = data.get("metadata", {})

    if not findings:
        print("No findings to validate.")
        return {"metadata": metadata, "findings": []}

    # Count findings above threshold
    big_findings = [f for f in findings if f.get("monthly_savings", 0) > threshold]
    print(f"\nFound {len(findings)} findings, {len(big_findings)} above ${threshold} threshold")
    if big_findings:
        print(f"Will query AWS Pricing API for {len(big_findings)} finding(s)...\n")

    # Cache for service spend (avoid repeated Cost Explorer queries)
    service_spend_cache = {}

    # Correct each finding
    corrected_findings = []
    total_original = 0
    total_corrected = 0
    api_validated_count = 0
    sanity_check_corrections = 0

    for finding in findings:
        original = finding.get("monthly_savings", 0)
        total_original += original

        # Step 1: Price correction
        corrected = correct_finding(finding.copy(), profile, threshold)

        # Step 2: MANDATORY sanity check (savings <= service spend)
        corrected = sanity_check_finding(corrected, profile, service_spend_cache)
        if corrected.get("pricing_corrected"):
            sanity_check_corrections += 1

        corrected_findings.append(corrected)
        total_corrected += corrected.get("monthly_savings", 0)

        if corrected.get("pricing_validated", {}).get("api_validated"):
            api_validated_count += 1

    source_counts = {
        "aws_pricing_api": 0,
        "verified_table": 0,
        "aws_cost_explorer": 0,
        "pricing_unknown": 0,
    }
    for corrected in corrected_findings:
        source = corrected.get("pricing_validated", {}).get("source")
        if source in source_counts:
            source_counts[source] += 1

    # Update metadata
    metadata["total_monthly_savings"] = round(total_corrected, 2)
    metadata["pricing_validation"] = {
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "validation_threshold": threshold,
        "api_validated_count": api_validated_count,
        "verified_table_count": source_counts["verified_table"],
        "cost_explorer_count": source_counts["aws_cost_explorer"],
        "pricing_unknown_count": source_counts["pricing_unknown"],
        "sanity_check_corrections": sanity_check_corrections,
        "service_spend_cache": {k: round(v, 2) if v else None for k, v in service_spend_cache.items()},
        "original_total": round(total_original, 2),
        "corrected_total": round(total_corrected, 2),
        "findings_processed": len(corrected_findings)
    }

    return {
        "metadata": metadata,
        "findings": corrected_findings
    }


def print_summary(data: dict) -> None:
    """Print correction summary."""
    validation = data.get("metadata", {}).get("pricing_validation", {})

    print("\n" + "=" * 60)
    print("AWS PRICING VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Findings Processed:   {validation.get('findings_processed', 0)}")
    print(f"API Validated:        {validation.get('api_validated_count', 0)} (>${validation.get('validation_threshold', 100)})")
    print(f"Verified Table:       {validation.get('verified_table_count', 0)}")
    print(f"Cost Explorer:        {validation.get('cost_explorer_count', 0)}")
    print(f"Pricing Unknown:      {validation.get('pricing_unknown_count', 0)}")
    print("-" * 60)
    print(f"Original Total:       ${validation.get('original_total', 0):,.2f}/month")
    print(f"Validated Total:      ${validation.get('corrected_total', 0):,.2f}/month")

    diff = validation.get('corrected_total', 0) - validation.get('original_total', 0)
    if diff > 0:
        print(f"Adjustment:           +${diff:,.2f} (estimates were low)")
    elif diff < 0:
        print(f"Adjustment:           -${abs(diff):,.2f} (estimates were high)")
    else:
        print(f"Adjustment:           $0.00 (estimates were accurate)")

    print("=" * 60)

    # Show API-validated findings
    findings = data.get("findings", [])
    api_validated = [f for f in findings if f.get("pricing_validated", {}).get("api_validated")]

    if api_validated:
        print("\nAPI-Validated Findings:")
        print("-" * 60)
        for f in sorted(api_validated, key=lambda x: x.get("monthly_savings", 0), reverse=True):
            pv = f.get("pricing_validated", {})
            print(f"  {f.get('check_id')}: {f.get('title', '')[:40]}")
            print(f"    ${pv.get('original_estimate', 0):.2f} -> ${f.get('monthly_savings', 0):.2f} ({pv.get('source', 'unknown')})")

    # Show significant corrections
    corrections = []
    for f in findings:
        pv = f.get("pricing_validated", {})
        original = pv.get("original_estimate", 0)
        corrected = f.get("monthly_savings", 0)
        if original > 0 and abs(corrected - original) / original > 0.15:
            corrections.append({
                "check_id": f.get("check_id"),
                "title": f.get("title", "")[:40],
                "original": original,
                "corrected": corrected
            })

    if corrections:
        print("\nSignificant Price Corrections (>15% change):")
        print("-" * 60)
        for c in sorted(corrections, key=lambda x: abs(x["corrected"] - x["original"]), reverse=True)[:10]:
            print(f"  {c['check_id']}: {c['title']}")
            print(f"    ${c['original']:.2f} -> ${c['corrected']:.2f}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Validate AWS cost findings with real Pricing API (for big findings)"
    )
    parser.add_argument("findings", help="Path to findings.json")
    parser.add_argument("--profile", default="", help="AWS profile (optional - uses default credentials if not specified)")
    parser.add_argument("--threshold", type=float, default=100, help="Only query Pricing API for findings > threshold (default: $100)")
    parser.add_argument("--output", help="Output path (default: overwrite input)")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")

    args = parser.parse_args()

    print(f"Validating findings: {args.findings}")
    print(f"AWS profile: {args.profile or '(default credentials)'}")
    print(f"API threshold: ${args.threshold} (only query API for larger findings)")

    corrected = correct_findings(args.findings, args.profile, args.threshold)
    print_summary(corrected)

    if not args.dry_run:
        output_path = args.output or args.findings
        with open(output_path, "w") as f:
            json.dump(corrected, f, indent=2)
        print(f"Validated findings saved to: {output_path}")


if __name__ == "__main__":
    main()
