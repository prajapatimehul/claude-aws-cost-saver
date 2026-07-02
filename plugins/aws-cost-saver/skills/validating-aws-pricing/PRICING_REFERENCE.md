# AWS Pricing API Reference

Complete reference for querying AWS pricing data.

---

## CRITICAL: Common Pricing Mistakes

**READ THIS FIRST** to avoid incorrect savings estimates.

### Mistake 1: CloudWatch Logs - Wrong Price Component

| Component | Price | When Charged | Affected by Retention? |
|-----------|-------|--------------|------------------------|
| **Ingestion** | $0.50/GB | Once, when logs arrive | NO |
| **Storage** | $0.03/GB-month | Monthly, for stored data | YES |

**WRONG:** `savings = stored_gb × $0.50` (uses ingestion price)
**CORRECT:** `savings = stored_gb × $0.03` (uses storage price)

### Mistake 2: Savings Exceed Service Spend

**ALWAYS verify:** `finding.monthly_savings <= service.monthly_spend`

If CloudWatch costs $159/mo total, you CANNOT save $594/mo on it.

### Mistake 3: Not Checking Usage Type Breakdown

For findings > $100, query Cost Explorer with `USAGE_TYPE` grouping:
```bash
aws ce get-cost-and-usage \
  --filter '{"Dimensions": {"Key": "SERVICE", "Values": ["AmazonCloudWatch"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE
```

This shows actual storage vs ingestion costs.

### Mistake 4: Using Multipliers Without Understanding

Never use arbitrary multipliers. Always trace back to the formula:

| Finding Type | Formula |
|--------------|---------|
| CW Logs Retention | `stored_gb × $0.03` |
| EBS Volume | `size_gb × price_per_gb` |
| EC2 Instance | `hourly_rate × 730` |
| RDS Downsize | `(current_hourly - new_hourly) × 730` |

---

## API Basics

### Endpoint Regions

The AWS Pricing API is only available in two regions:
- `us-east-1` (N. Virginia)
- `ap-south-1` (Mumbai)

Always use `--region us-east-1` for pricing queries.

### Location Values

Map AWS regions to pricing location names:

| Region Code | Location Name |
|-------------|---------------|
| us-east-1 | US East (N. Virginia) |
| us-east-2 | US East (Ohio) |
| us-west-1 | US West (N. California) |
| us-west-2 | US West (Oregon) |
| eu-west-1 | EU (Ireland) |
| eu-central-1 | EU (Frankfurt) |
| ap-southeast-1 | Asia Pacific (Singapore) |
| ap-northeast-1 | Asia Pacific (Tokyo) |

## Service Codes

| Service | Service Code |
|---------|--------------|
| EC2 | AmazonEC2 |
| RDS | AmazonRDS |
| ElastiCache | AmazonElastiCache |
| Lambda | AWSLambda |
| S3 | AmazonS3 |
| CloudWatch | AmazonCloudWatch |
| DynamoDB | AmazonDynamoDB |
| EFS | AmazonEFS |

## EC2 Pricing Queries

### Instance Pricing

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonEC2 \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value=t2.nano" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
    "Type=TERM_MATCH,Field=operatingSystem,Value=Linux" \
    "Type=TERM_MATCH,Field=tenancy,Value=Shared" \
    "Type=TERM_MATCH,Field=preInstalledSw,Value=NA" \
    "Type=TERM_MATCH,Field=capacitystatus,Value=Used" \
  --max-results 1
```

**Filter Fields:**
- `instanceType`: e.g., t2.nano, m5.large, r5.xlarge
- `operatingSystem`: Linux, Windows, RHEL, SUSE
- `tenancy`: Shared, Dedicated, Host
- `preInstalledSw`: NA, SQL Std, SQL Web, SQL Ent
- `capacitystatus`: Used, UnusedCapacityReservation

### EBS Volume Pricing

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonEC2 \
  --filters \
    "Type=TERM_MATCH,Field=volumeApiName,Value=gp3" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
  --max-results 5
```

**Volume Types:**
- `gp3`: General Purpose SSD (latest)
- `gp2`: General Purpose SSD (previous gen)
- `io2`: Provisioned IOPS SSD
- `io1`: Provisioned IOPS SSD (previous gen)
- `st1`: Throughput Optimized HDD
- `sc1`: Cold HDD
- `standard`: Magnetic

**gp3 Pricing Components:**
- Storage: $0.08 per GB-month
- IOPS: $0.005 per IOPS-month (above 3000 baseline)
- Throughput: $0.04 per MB/s-month (above 125 MB/s baseline)

### EBS Snapshot Pricing

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonEC2 \
  --filters \
    "Type=TERM_MATCH,Field=productFamily,Value=Storage Snapshot" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
  --max-results 1
```

## RDS Pricing Queries

### Instance Pricing

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonRDS \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value=db.r5.xlarge" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
    "Type=TERM_MATCH,Field=databaseEngine,Value=PostgreSQL" \
    "Type=TERM_MATCH,Field=deploymentOption,Value=Single-AZ" \
  --max-results 1
```

**Database Engines:**
- PostgreSQL
- MySQL
- MariaDB
- Oracle
- SQL Server
- Aurora MySQL
- Aurora PostgreSQL

**Deployment Options:**
- Single-AZ
- Multi-AZ

### RDS Storage Pricing

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonRDS \
  --filters \
    "Type=TERM_MATCH,Field=productFamily,Value=Database Storage" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
    "Type=TERM_MATCH,Field=volumeType,Value=General Purpose (SSD)" \
  --max-results 1
```

## ElastiCache Pricing Queries

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonElastiCache \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value=cache.t3.small" \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
    "Type=TERM_MATCH,Field=cacheEngine,Value=Redis" \
  --max-results 1
```

**Cache Engines:**
- Redis
- Memcached
- Valkey

## Lambda Pricing Queries

```bash
aws pricing get-products --region us-east-1 \
  --service-code AWSLambda \
  --filters \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
  --max-results 10
```

**Lambda Pricing Model:**
- Requests: $0.20 per 1M requests
- Duration: $0.0000166667 per GB-second (x86)
- Duration: $0.0000133334 per GB-second (ARM64) - 20% cheaper

**Calculate Lambda Cost:**
```python
def calculate_lambda_cost(memory_mb, duration_ms, invocations, architecture='x86'):
    gb_seconds = (memory_mb / 1024) * (duration_ms / 1000) * invocations

    if architecture == 'arm64':
        duration_cost = gb_seconds * 0.0000133334
    else:
        duration_cost = gb_seconds * 0.0000166667

    request_cost = (invocations / 1_000_000) * 0.20
    return duration_cost + request_cost
```

## CloudWatch Pricing Queries

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonCloudWatch \
  --filters \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
  --max-results 20
```

**CloudWatch Logs Pricing:**
- Ingestion: $0.50 per GB (first 10TB)
- Storage: $0.03 per GB-month
- Insights queries: $0.005 per GB scanned

## S3 Pricing Queries

```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonS3 \
  --filters \
    "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" \
    "Type=TERM_MATCH,Field=storageClass,Value=General Purpose" \
  --max-results 5
```

**Storage Classes:**
- General Purpose (Standard)
- Infrequent Access
- One Zone-IA
- Glacier Instant Retrieval
- Glacier Flexible Retrieval
- Glacier Deep Archive
- Intelligent-Tiering

## Parsing Pricing Responses

### Response Structure

```json
{
  "PriceList": [
    "{\"product\":{...},\"terms\":{\"OnDemand\":{...},\"Reserved\":{...}}}"
  ],
  "FormatVersion": "aws_v1"
}
```

### Extract On-Demand Price

```python
import json

def parse_pricing_response(response_json):
    """Parse AWS Pricing API response and extract On-Demand price."""
    data = json.loads(response_json)
    price_list = data.get('PriceList', [])

    if not price_list:
        return None

    # Parse the nested JSON string
    product_data = json.loads(price_list[0])

    # Navigate to On-Demand pricing
    on_demand = product_data.get('terms', {}).get('OnDemand', {})

    for sku_term in on_demand.values():
        price_dimensions = sku_term.get('priceDimensions', {})
        for dimension in price_dimensions.values():
            price_per_unit = dimension.get('pricePerUnit', {})
            usd_price = price_per_unit.get('USD')
            if usd_price:
                return {
                    'price': float(usd_price),
                    'unit': dimension.get('unit'),
                    'description': dimension.get('description')
                }

    return None
```

### Extract Reserved Instance Price

```python
def parse_reserved_pricing(response_json, term_length='1yr', purchase_option='All Upfront'):
    """Extract Reserved Instance pricing for comparison."""
    data = json.loads(response_json)
    price_list = data.get('PriceList', [])

    if not price_list:
        return None

    product_data = json.loads(price_list[0])
    reserved = product_data.get('terms', {}).get('Reserved', {})

    for sku_term in reserved.values():
        attrs = sku_term.get('termAttributes', {})
        if (attrs.get('LeaseContractLength') == term_length and
            attrs.get('PurchaseOption') == purchase_option):

            upfront = 0
            hourly = 0

            for dim in sku_term.get('priceDimensions', {}).values():
                if 'Upfront' in dim.get('description', ''):
                    upfront = float(dim['pricePerUnit'].get('USD', 0))
                else:
                    hourly = float(dim['pricePerUnit'].get('USD', 0))

            return {
                'upfront': upfront,
                'hourly': hourly,
                'term': term_length,
                'option': purchase_option
            }

    return None
```

## Cost Calculation Formulas

### Monthly Hours

```python
HOURS_PER_MONTH = 730  # 365 days * 24 hours / 12 months
```

### EC2 Monthly Cost

```python
def ec2_monthly_cost(hourly_rate):
    return hourly_rate * 730
```

### EBS Monthly Cost

```python
def ebs_monthly_cost(size_gb, volume_type='gp3', iops=3000, throughput=125):
    if volume_type == 'gp3':
        storage_cost = size_gb * 0.08
        iops_cost = max(0, iops - 3000) * 0.005
        throughput_cost = max(0, throughput - 125) * 0.04
        return storage_cost + iops_cost + throughput_cost
    elif volume_type == 'gp2':
        return size_gb * 0.10
    # Add other volume types as needed
```

### RDS Monthly Cost

```python
def rds_monthly_cost(hourly_rate, storage_gb, multi_az=False):
    compute_cost = hourly_rate * 730
    storage_cost = storage_gb * 0.115  # gp2 storage
    if multi_az:
        compute_cost *= 2
        storage_cost *= 2
    return compute_cost + storage_cost
```

### Reserved Instance Savings

```python
def ri_savings(on_demand_hourly, ri_upfront, ri_hourly, term_years=1):
    """Calculate monthly savings with Reserved Instance."""
    on_demand_monthly = on_demand_hourly * 730
    ri_monthly = (ri_upfront / (term_years * 12)) + (ri_hourly * 730)
    savings = on_demand_monthly - ri_monthly
    savings_percent = (savings / on_demand_monthly) * 100
    return {
        'monthly_savings': savings,
        'savings_percent': savings_percent
    }
```

## Common Pricing (us-east-1, verified July 2026)

### EC2 Instances

| Instance Type | Hourly | Monthly |
|---------------|--------|---------|
| t2.nano | $0.0058 | $4.23 |
| t2.micro | $0.0116 | $8.47 |
| t3.nano | $0.0052 | $3.80 |
| t3.micro | $0.0104 | $7.59 |
| t3.small | $0.0208 | $15.18 |
| m5.large | $0.096 | $70.08 |
| m5.xlarge | $0.192 | $140.16 |
| r5.large | $0.126 | $91.98 |
| r5.xlarge | $0.252 | $183.96 |

### RDS Instances (PostgreSQL, Single-AZ)

| Instance Type | Hourly | Monthly |
|---------------|--------|---------|
| db.t3.micro | $0.017 | $12.41 |
| db.t3.small | $0.034 | $24.82 |
| db.t3.medium | $0.068 | $49.64 |
| db.r5.large | $0.25 | $182.50 |
| db.r5.xlarge | $0.50 | $365.00 |
| db.r5.2xlarge | $1.00 | $730.00 |

### ElastiCache (Redis OSS)

| Node Type | Hourly | Monthly |
|-----------|--------|---------|
| cache.t3.micro | $0.017 | $12.41 |
| cache.t3.small | $0.034 | $24.82 |
| cache.t3.medium | $0.068 | $49.64 |
| cache.r5.large | $0.182 | $132.86 |

**ElastiCache notes (2026):**
- Valkey engine nodes are ~20% cheaper than the Redis OSS prices above (serverless ~33%).
- Redis OSS 4.x/5.x nodes pay an **extended-support surcharge of +80%** since Feb 2026 (+160% from Feb 2028); Redis OSS 6.x follows Feb 2027.
- Latest Graviton node generation is m7g/r7g — `cache.m8g`/`cache.r8g` do NOT exist.

### Storage

| Type | Price | Unit |
|------|-------|------|
| EBS gp3 | $0.08 | GB-month (+$0.005/IOPS >3000, +$0.04/MBps >125) |
| EBS gp2 | $0.10 | GB-month |
| EBS io2 | $0.125 | GB-month + tiered IOPS: $0.065 (first 32K), $0.046 (32K-64K), $0.032 (>64K) |
| S3 Standard | $0.023 | GB-month (first 50TB) |
| S3 IA | $0.0125 | GB-month |
| EFS | $0.30 | GB-month |
| CloudWatch Logs storage | $0.03 | GB-month |
| CloudWatch Logs ingestion | $0.50 | GB first 10TB/mo, tiered down to $0.05 at scale (May 2025) |

Note: gp3 now supports up to 64 TiB / 80,000 IOPS / 2,000 MiB/s (Sep 2025), so most io1/io2 volumes can migrate to gp3.

### Other Verified Rates (2026)

| Item | Price | Unit |
|------|-------|------|
| DynamoDB on-demand writes | $0.625 | per million WRU (halved Nov 2024 — do not use older prices) |
| DynamoDB on-demand reads | $0.125 | per million RRU (halved Nov 2024) |
| RDS Extended Support (year 1-2) | $0.100 | per vCPU-hour |
| RDS Extended Support (year 3+) | $0.200 | per vCPU-hour |
| EKS control plane (standard) | $0.10 | per hour ($73/mo) |
| EKS control plane (extended support) | $0.60 | per hour ($438/mo) |
