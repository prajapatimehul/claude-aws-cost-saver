"""
AWS Cost and Usage Report (CUR) Parser

Handles Parquet and CSV CUR files with:
- Multi-file export support (partitioned by date)
- Column normalization across CUR versions
- Memory-efficient processing for large files
- Metadata extraction
"""

import json
from typing import Dict, List, Optional, Any, Generator
from pathlib import Path

try:
    import pyarrow.parquet as pq
    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


# Column name mappings for normalization across CUR versions
COLUMN_MAPPINGS = {
    # Standard columns
    'lineItem/UsageStartDate': 'usage_start_date',
    'lineItem/UsageEndDate': 'usage_end_date',
    'lineItem/ProductCode': 'product_code',
    'lineItem/UsageType': 'usage_type',
    'lineItem/Operation': 'operation',
    'lineItem/ResourceId': 'resource_id',
    'lineItem/UsageAmount': 'usage_amount',
    'lineItem/UnblendedCost': 'unblended_cost',
    'lineItem/BlendedCost': 'blended_cost',
    'lineItem/LineItemType': 'line_item_type',

    # Account info
    'lineItem/UsageAccountId': 'usage_account_id',
    'bill/PayerAccountId': 'payer_account_id',
    'bill/BillingPeriodStartDate': 'billing_period_start',
    'bill/BillingPeriodEndDate': 'billing_period_end',

    # Product details
    'product/region': 'region',
    'product/instanceType': 'instance_type',
    'product/instanceFamily': 'instance_family',
    'product/operatingSystem': 'operating_system',
    'product/tenancy': 'tenancy',
    'product/volumeType': 'volume_type',
    'product/storageClass': 'storage_class',
    'product/databaseEngine': 'database_engine',

    # Pricing
    'pricing/publicOnDemandCost': 'public_ondemand_cost',
    'pricing/term': 'pricing_term',
    'pricing/unit': 'pricing_unit',

    # Reservations
    'reservation/ReservationARN': 'reservation_arn',
    'reservation/EffectiveCost': 'reservation_effective_cost',
    'reservation/UnusedQuantity': 'reservation_unused_quantity',
    'reservation/UnusedRecurringFee': 'reservation_unused_fee',

    # Savings Plans
    'savingsPlan/SavingsPlanARN': 'savings_plan_arn',
    'savingsPlan/SavingsPlanEffectiveCost': 'savings_plan_effective_cost',
    'savingsPlan/SavingsPlanRate': 'savings_plan_rate',
}

# Key columns needed for each domain analyzer
DOMAIN_COLUMNS = {
    'compute': [
        'usage_start_date', 'resource_id', 'product_code', 'instance_type',
        'unblended_cost', 'usage_amount', 'region', 'usage_account_id',
        'operating_system', 'tenancy', 'pricing_term', 'line_item_type'
    ],
    'storage': [
        'usage_start_date', 'resource_id', 'product_code', 'usage_type',
        'unblended_cost', 'usage_amount', 'region', 'usage_account_id',
        'volume_type', 'storage_class'
    ],
    'database': [
        'usage_start_date', 'resource_id', 'product_code', 'instance_type',
        'unblended_cost', 'usage_amount', 'region', 'usage_account_id',
        'database_engine'
    ],
    'networking': [
        'usage_start_date', 'resource_id', 'product_code', 'usage_type',
        'operation', 'unblended_cost', 'usage_amount', 'region'
    ],
    'serverless': [
        'usage_start_date', 'resource_id', 'product_code', 'usage_type',
        'unblended_cost', 'usage_amount', 'region'
    ],
    'reservations': [
        'usage_start_date', 'resource_id', 'product_code', 'reservation_arn',
        'reservation_effective_cost', 'reservation_unused_quantity',
        'savings_plan_arn', 'savings_plan_effective_cost', 'pricing_term',
        'unblended_cost', 'public_ondemand_cost'
    ]
}

# Service codes for filtering
SERVICE_CODES = {
    'compute': ['AmazonEC2', 'AmazonECS', 'AmazonEKS'],
    'storage': ['AmazonS3', 'AmazonEBS', 'AmazonEFS', 'AmazonGlacier'],
    'database': ['AmazonRDS', 'AmazonDynamoDB', 'AmazonElastiCache', 'AmazonRedshift', 'AmazonDocDB', 'AmazonNeptune'],
    'networking': ['AmazonVPC', 'AmazonCloudFront', 'AmazonRoute53', 'AWSDataTransfer', 'AWSELB'],
    'serverless': ['AWSLambda', 'AmazonApiGateway', 'AmazonSQS', 'AmazonSNS', 'AWSStepFunctions'],
    'reservations': ['AmazonEC2', 'AmazonRDS', 'AmazonElastiCache', 'AmazonRedshift', 'AmazonES'],
    'tags': None,  # All services
    'security': ['AmazonCloudWatch', 'AWSCloudTrail', 'AWSKMS', 'AWSSecurityHub', 'AmazonGuardDuty']
}


class CURParser:
    """Parser for AWS Cost and Usage Report files."""

    def __init__(self, data_path: str, config: Optional[Dict] = None):
        """
        Initialize CUR parser.

        Args:
            data_path: Path to raw-data folder containing CUR files
            config: Optional client configuration
        """
        self.data_path = Path(data_path)
        self.config = config or {}
        self.metadata = {}
        self._files = []
        self._column_map = {}

    def discover_files(self) -> List[Path]:
        """Discover all CUR files in the data path."""
        self._files = []

        # Look for Parquet files
        parquet_files = list(self.data_path.glob('**/*.parquet'))
        if parquet_files:
            self._files.extend(parquet_files)
            self.metadata['format'] = 'parquet'

        # Look for CSV files if no Parquet found
        if not self._files:
            csv_files = list(self.data_path.glob('**/*.csv'))
            csv_files.extend(self.data_path.glob('**/*.csv.gz'))
            if csv_files:
                self._files.extend(csv_files)
                self.metadata['format'] = 'csv'

        self.metadata['file_count'] = len(self._files)
        self.metadata['total_size_mb'] = sum(f.stat().st_size for f in self._files) / (1024 * 1024)

        return self._files

    def _normalize_column_name(self, col: str) -> str:
        """Normalize column name to standard format."""
        # Check if it's in our mapping
        if col in COLUMN_MAPPINGS:
            return COLUMN_MAPPINGS[col]

        # Handle tag columns
        if col.startswith('resourceTags/'):
            tag_name = col.split('/')[-1]
            return f'tag_{tag_name.lower()}'

        # Default: convert to snake_case
        normalized = col.replace('/', '_').replace(':', '_')
        # Convert camelCase to snake_case
        result = []
        for i, char in enumerate(normalized):
            if char.isupper() and i > 0:
                result.append('_')
            result.append(char.lower())
        return ''.join(result)

    def _build_column_map(self, columns: List[str]) -> Dict[str, str]:
        """Build mapping from original to normalized column names."""
        return {col: self._normalize_column_name(col) for col in columns}

    def get_schema(self) -> Dict[str, str]:
        """Get the schema of the CUR files."""
        if not self._files:
            self.discover_files()

        if not self._files:
            return {}

        sample_file = self._files[0]

        if self.metadata.get('format') == 'parquet' and PYARROW_AVAILABLE:
            schema = pq.read_schema(sample_file)
            columns = [field.name for field in schema]
        elif PANDAS_AVAILABLE:
            # Read just the header for CSV
            df = pd.read_csv(sample_file, nrows=0)
            columns = list(df.columns)
        else:
            raise ImportError("Either pyarrow or pandas is required")

        self._column_map = self._build_column_map(columns)
        return self._column_map

    def extract_metadata(self) -> Dict[str, Any]:
        """Extract metadata from CUR files."""
        if not self._files:
            self.discover_files()

        if not self._files:
            return {'error': 'No CUR files found'}

        # Read a sample to get date range and accounts
        sample_data = self._read_sample(1000)

        if sample_data is not None and not sample_data.empty:
            # Get date range
            if 'usage_start_date' in sample_data.columns:
                dates = pd.to_datetime(sample_data['usage_start_date'])
                self.metadata['date_range'] = {
                    'start': dates.min().isoformat(),
                    'end': dates.max().isoformat()
                }

            # Get accounts
            if 'usage_account_id' in sample_data.columns:
                self.metadata['accounts'] = sample_data['usage_account_id'].dropna().unique().tolist()

            # Get services
            if 'product_code' in sample_data.columns:
                self.metadata['services'] = sample_data['product_code'].dropna().unique().tolist()

            # Get regions
            if 'region' in sample_data.columns:
                self.metadata['regions'] = sample_data['region'].dropna().unique().tolist()

        return self.metadata

    def _read_sample(self, n_rows: int = 1000):
        """Read a sample of rows for metadata extraction."""
        if not self._files:
            return None

        if not self._column_map:
            self.get_schema()

        sample_file = self._files[0]

        if self.metadata.get('format') == 'parquet' and PYARROW_AVAILABLE:
            table = pq.read_table(sample_file).slice(0, n_rows)
            df = table.to_pandas()
        elif PANDAS_AVAILABLE:
            df = pd.read_csv(sample_file, nrows=n_rows)
        else:
            raise ImportError("Either pyarrow or pandas is required")

        # Rename columns
        df = df.rename(columns=self._column_map)
        return df

    def parse(self, domain: Optional[str] = None,
              filters: Optional[Dict] = None,
              chunk_size: int = 100000) -> Generator:
        """
        Parse CUR files with optional filtering.

        Args:
            domain: Optional domain to filter columns and services
            filters: Optional dict of column:value filters
            chunk_size: Number of rows per chunk for memory efficiency

        Yields:
            Pandas DataFrames in chunks
        """
        if not self._files:
            self.discover_files()

        if not self._files:
            raise ValueError(f"No CUR files found in {self.data_path}")

        if not self._column_map:
            self.get_schema()

        # Determine columns to read
        columns_to_read = None
        if domain and domain in DOMAIN_COLUMNS:
            columns_to_read = DOMAIN_COLUMNS[domain]

        # Determine service filter
        service_filter = None
        if domain and domain in SERVICE_CODES and SERVICE_CODES[domain]:
            service_filter = SERVICE_CODES[domain]

        for file_path in self._files:
            yield from self._parse_file(
                file_path,
                columns_to_read,
                service_filter,
                filters,
                chunk_size
            )

    def _parse_file(self, file_path: Path, columns: Optional[List[str]],
                    service_filter: Optional[List[str]],
                    filters: Optional[Dict],
                    chunk_size: int) -> Generator:
        """Parse a single CUR file."""

        # Reverse map to get original column names
        reverse_map = {v: k for k, v in self._column_map.items()}

        if self.metadata.get('format') == 'parquet' and PYARROW_AVAILABLE:
            yield from self._parse_parquet(
                file_path, columns, service_filter, filters, chunk_size, reverse_map
            )
        elif PANDAS_AVAILABLE:
            yield from self._parse_csv(
                file_path, columns, service_filter, filters, chunk_size, reverse_map
            )
        else:
            raise ImportError("Either pyarrow or pandas is required")

    def _parse_parquet(self, file_path: Path, columns: Optional[List[str]],
                       service_filter: Optional[List[str]],
                       filters: Optional[Dict],
                       chunk_size: int, reverse_map: Dict) -> Generator:
        """Parse a Parquet file."""

        # Get original column names to read
        original_columns = None
        if columns:
            original_columns = [reverse_map.get(c) for c in columns if reverse_map.get(c)]
            # Always include product_code for filtering
            product_code_orig = reverse_map.get('product_code')
            if product_code_orig and product_code_orig not in original_columns:
                original_columns.append(product_code_orig)

        # Read parquet file
        table = pq.read_table(file_path, columns=original_columns)
        df = table.to_pandas()

        # Rename columns
        df = df.rename(columns=self._column_map)

        # Apply service filter
        if service_filter and 'product_code' in df.columns:
            df = df[df['product_code'].isin(service_filter)]

        # Apply custom filters
        if filters:
            for col, value in filters.items():
                if col in df.columns:
                    if isinstance(value, list):
                        df = df[df[col].isin(value)]
                    else:
                        df = df[df[col] == value]

        # Yield in chunks
        for i in range(0, len(df), chunk_size):
            yield df.iloc[i:i+chunk_size]

    def _parse_csv(self, file_path: Path, columns: Optional[List[str]],
                   service_filter: Optional[List[str]],
                   filters: Optional[Dict],
                   chunk_size: int, reverse_map: Dict) -> Generator:
        """Parse a CSV file."""

        # Determine compression
        compression = 'gzip' if str(file_path).endswith('.gz') else None

        # Get original column names to read
        original_columns = None
        if columns:
            original_columns = [reverse_map.get(c) for c in columns if reverse_map.get(c)]
            product_code_orig = reverse_map.get('product_code')
            if product_code_orig and product_code_orig not in original_columns:
                original_columns.append(product_code_orig)

        # Read in chunks
        for chunk in pd.read_csv(file_path, chunksize=chunk_size,
                                  usecols=original_columns, compression=compression):
            # Rename columns
            chunk = chunk.rename(columns=self._column_map)

            # Apply service filter
            if service_filter and 'product_code' in chunk.columns:
                chunk = chunk[chunk['product_code'].isin(service_filter)]

            # Apply custom filters
            if filters:
                for col, value in filters.items():
                    if col in chunk.columns:
                        if isinstance(value, list):
                            chunk = chunk[chunk[col].isin(value)]
                        else:
                            chunk = chunk[chunk[col] == value]

            if len(chunk) > 0:
                yield chunk

    def parse_to_dataframe(self, domain: Optional[str] = None,
                           filters: Optional[Dict] = None,
                           max_rows: Optional[int] = None) -> 'pd.DataFrame':
        """
        Parse CUR files and return a single DataFrame.

        Warning: May consume significant memory for large CUR files.

        Args:
            domain: Optional domain to filter
            filters: Optional filters
            max_rows: Optional maximum rows to return

        Returns:
            Pandas DataFrame with all data
        """
        chunks = []
        total_rows = 0

        for chunk in self.parse(domain=domain, filters=filters):
            if max_rows and total_rows + len(chunk) > max_rows:
                chunks.append(chunk.iloc[:max_rows - total_rows])
                break
            chunks.append(chunk)
            total_rows += len(chunk)

        if chunks:
            return pd.concat(chunks, ignore_index=True)
        return pd.DataFrame()

    def get_resource_costs(self, domain: Optional[str] = None) -> Dict[str, float]:
        """
        Aggregate costs by resource ID.

        Args:
            domain: Optional domain to filter

        Returns:
            Dict of resource_id -> total_cost
        """
        resource_costs = {}

        for chunk in self.parse(domain=domain):
            if 'resource_id' in chunk.columns and 'unblended_cost' in chunk.columns:
                grouped = chunk.groupby('resource_id')['unblended_cost'].sum()
                for resource_id, cost in grouped.items():
                    if pd.notna(resource_id) and resource_id:
                        resource_costs[resource_id] = resource_costs.get(resource_id, 0) + cost

        return resource_costs

    def get_service_costs(self) -> Dict[str, float]:
        """Get costs aggregated by service."""
        service_costs = {}

        for chunk in self.parse():
            if 'product_code' in chunk.columns and 'unblended_cost' in chunk.columns:
                grouped = chunk.groupby('product_code')['unblended_cost'].sum()
                for service, cost in grouped.items():
                    if pd.notna(service):
                        service_costs[service] = service_costs.get(service, 0) + cost

        return service_costs

    def get_daily_costs(self, domain: Optional[str] = None) -> Dict[str, float]:
        """Get costs aggregated by day."""
        daily_costs = {}

        for chunk in self.parse(domain=domain):
            if 'usage_start_date' in chunk.columns and 'unblended_cost' in chunk.columns:
                chunk['date'] = pd.to_datetime(chunk['usage_start_date']).dt.date.astype(str)
                grouped = chunk.groupby('date')['unblended_cost'].sum()
                for date, cost in grouped.items():
                    daily_costs[date] = daily_costs.get(date, 0) + cost

        return dict(sorted(daily_costs.items()))

    def get_tags_usage(self) -> Dict[str, Dict[str, int]]:
        """Analyze tag usage across resources."""
        tag_usage = {}

        for chunk in self.parse():
            tag_columns = [c for c in chunk.columns if c.startswith('tag_')]
            for tag_col in tag_columns:
                tag_name = tag_col.replace('tag_', '')
                if tag_name not in tag_usage:
                    tag_usage[tag_name] = {'tagged': 0, 'untagged': 0}

                tagged = chunk[tag_col].notna().sum()
                untagged = chunk[tag_col].isna().sum()
                tag_usage[tag_name]['tagged'] += tagged
                tag_usage[tag_name]['untagged'] += untagged

        return tag_usage

    def validate(self) -> Dict[str, Any]:
        """Validate CUR files and return validation results."""
        results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'summary': {}
        }

        if not self._files:
            self.discover_files()

        if not self._files:
            results['valid'] = False
            results['errors'].append('No CUR files found')
            return results

        # Check schema
        try:
            schema = self.get_schema()
            normalized_cols = set(schema.values())

            # Check for required columns
            required = ['usage_start_date', 'product_code', 'unblended_cost']
            missing = [col for col in required if col not in normalized_cols]
            if missing:
                results['valid'] = False
                results['errors'].append(f'Missing required columns: {missing}')

            # Check for recommended columns
            recommended = ['resource_id', 'region', 'usage_account_id']
            missing_recommended = [col for col in recommended if col not in normalized_cols]
            if missing_recommended:
                results['warnings'].append(f'Missing recommended columns: {missing_recommended}')

        except Exception as e:
            results['valid'] = False
            results['errors'].append(f'Schema extraction failed: {str(e)}')
            return results

        # Check data integrity
        try:
            metadata = self.extract_metadata()
            results['summary'] = metadata

            if not metadata.get('date_range'):
                results['warnings'].append('Could not determine date range')

            if not metadata.get('accounts'):
                results['warnings'].append('No account IDs found')

        except Exception as e:
            results['warnings'].append(f'Metadata extraction warning: {str(e)}')

        return results


def create_parser(client_path: str) -> CURParser:
    """
    Factory function to create a CUR parser for a client.

    Args:
        client_path: Path to client folder

    Returns:
        Configured CURParser instance
    """
    client_path = Path(client_path)
    data_path = client_path / 'raw-data'
    config_path = client_path / 'config.json'

    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)

    return CURParser(str(data_path), config)
