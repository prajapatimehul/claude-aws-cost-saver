# AWS Pricing API Query Reference

Service-specific `aws pricing get-products` templates and the full region
location map. Used by the aws-cost-saver agent when pricing resources beyond
EC2/RDS/EBS (whose templates are inline in the agent prompt).

All queries run against the Pricing API endpoint with `--region us-east-1`.
Parse responses the same way for every service:
`PriceList[0] → JSON → terms.OnDemand → first value → priceDimensions → first value → pricePerUnit.USD`,
then `monthly_cost = hourly_price × 730`.

Tip: the Pricing API also supports `EQUALS`, `CONTAINS`, `ANY_OF`, and
`NONE_OF` filter types (since Jul 2025). Use `ANY_OF` on `instanceType` to
batch several SKU lookups into one call.

## Service Query Templates

### ElastiCache
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonElastiCache \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_NODE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
    "Type=TERM_MATCH,Field=cacheEngine,Value={ACTUAL_ENGINE}" \
  --max-results 1
```

### OpenSearch
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonES \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_INSTANCE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### DocumentDB
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonDocDB \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_INSTANCE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### Neptune
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonNeptune \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_INSTANCE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### Redshift
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonRedshift \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_NODE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### SageMaker
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonSageMaker \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_INSTANCE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### MSK (Kafka)
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonMSK \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value={ACTUAL_INSTANCE_TYPE}" \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 1
```

### FSx
```bash
aws pricing get-products --region us-east-1 \
  --service-code AmazonFSx \
  --filters \
    "Type=TERM_MATCH,Field=location,Value={LOCATION_NAME}" \
  --max-results 10
```

## Region Location Map (COMPLETE)

| Region Code | Location Name |
|-------------|---------------|
| us-east-1 | US East (N. Virginia) |
| us-east-2 | US East (Ohio) |
| us-west-1 | US West (N. California) |
| us-west-2 | US West (Oregon) |
| eu-west-1 | EU (Ireland) |
| eu-west-2 | EU (London) |
| eu-west-3 | EU (Paris) |
| eu-central-1 | EU (Frankfurt) |
| eu-central-2 | EU (Zurich) |
| eu-north-1 | EU (Stockholm) |
| eu-south-1 | EU (Milan) |
| ap-southeast-1 | Asia Pacific (Singapore) |
| ap-southeast-2 | Asia Pacific (Sydney) |
| ap-northeast-1 | Asia Pacific (Tokyo) |
| ap-northeast-2 | Asia Pacific (Seoul) |
| ap-northeast-3 | Asia Pacific (Osaka) |
| ap-south-1 | Asia Pacific (Mumbai) |
| ap-east-1 | Asia Pacific (Hong Kong) |
| sa-east-1 | South America (Sao Paulo) |
| ca-central-1 | Canada (Central) |
| ca-west-1 | Canada West (Calgary) |
| me-south-1 | Middle East (Bahrain) |
| me-central-1 | Middle East (UAE) |
| af-south-1 | Africa (Cape Town) |
| il-central-1 | Israel (Tel Aviv) |
| ap-south-2 | Asia Pacific (Hyderabad) |
| ap-southeast-3 | Asia Pacific (Jakarta) |
| ap-southeast-4 | Asia Pacific (Melbourne) |
| eu-south-2 | Europe (Spain) |

If the region is NOT in this map, do NOT guess the location name. Set
`pricing_unknown: true`.
