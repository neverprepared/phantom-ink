---
name: data-pipeline
description: Build data transformation pipelines, ETL jobs, and analytical queries. Use when the user needs pandas/polars/SQL transformations, data validation, aggregation logic, CSV/JSON processing, or data quality checks.
---

# Data Pipeline

You build data transformation code — ETL pipelines, analytical queries, data validation, and processing scripts. Prioritize correctness, performance, and clear data flow.

## Execution Model

1. Understand the input format (CSV, JSON, Parquet, database table, API response)
2. Understand the desired output format and schema
3. Identify transformations: filter, map, join, aggregate, pivot, window
4. Generate the pipeline code in one pass
5. Include sample input/output to verify correctness

## Library Preferences

| Task | Python | SQL | TypeScript |
|------|--------|-----|------------|
| Tabular transforms | **polars** (default), pandas if existing code uses it | CTE chains | — |
| Streaming/large files | polars lazy, DuckDB | — | Node streams |
| Validation | pandera, pydantic | CHECK constraints | zod |
| Orchestration | prefect, dagster | dbt | — |
| Visualization | plotly, altair | — | Observable Plot |

### Why Polars over Pandas
- 10-100x faster on large datasets
- Lazy evaluation with query optimization
- Null-safe (no NaN confusion)
- Consistent API (no `inplace=True` ambiguity)

Use pandas only when the user explicitly requests it or when integrating with pandas-only libraries.

## SQL Patterns

### CTE Chains (preferred over subqueries)
```sql
WITH
  raw AS (
    SELECT * FROM source_table WHERE created_at >= '2024-01-01'
  ),
  cleaned AS (
    SELECT
      id,
      TRIM(LOWER(email)) AS email,
      COALESCE(name, 'Unknown') AS name,
      amount::numeric(10,2) AS amount
    FROM raw
    WHERE email IS NOT NULL
  ),
  aggregated AS (
    SELECT
      email,
      COUNT(*) AS order_count,
      SUM(amount) AS total_spent,
      MIN(created_at) AS first_order,
      MAX(created_at) AS last_order
    FROM cleaned
    GROUP BY email
  )
SELECT *
FROM aggregated
WHERE order_count > 1
ORDER BY total_spent DESC;
```

### Window Functions
```sql
SELECT
  *,
  ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at DESC) AS recency_rank,
  SUM(amount) OVER (PARTITION BY customer_id ORDER BY created_at ROWS UNBOUNDED PRECEDING) AS running_total,
  LAG(amount) OVER (PARTITION BY customer_id ORDER BY created_at) AS prev_amount
FROM orders;
```

## Polars Patterns

### Lazy Pipeline
```python
import polars as pl

result = (
    pl.scan_csv("input.csv")
    .filter(pl.col("status") == "active")
    .with_columns(
        pl.col("email").str.to_lowercase().alias("email_clean"),
        pl.col("amount").cast(pl.Decimal(10, 2)),
        (pl.col("quantity") * pl.col("price")).alias("line_total"),
    )
    .group_by("customer_id")
    .agg(
        pl.col("line_total").sum().alias("total_spent"),
        pl.col("order_id").n_unique().alias("order_count"),
        pl.col("created_at").min().alias("first_order"),
        pl.col("created_at").max().alias("last_order"),
    )
    .filter(pl.col("order_count") > 1)
    .sort("total_spent", descending=True)
    .collect()
)
```

### Joins
```python
result = (
    orders.lazy()
    .join(customers.lazy(), on="customer_id", how="left")
    .join(products.lazy(), on="product_id", how="inner")
    .collect()
)
```

## Data Validation

Always validate data at pipeline boundaries (input and output).

### Schema Validation (Pandera)
```python
import pandera as pa
import pandera.polars as ppl

class InputSchema(ppl.DataFrameModel):
    email: str = ppl.Field(str_matches=r"^[\w.+-]+@[\w-]+\.[\w.]+$")
    amount: float = ppl.Field(ge=0, le=1_000_000)
    status: str = ppl.Field(isin=["active", "inactive", "pending"])
    created_at: datetime = ppl.Field(le=datetime.now())

    class Config:
        strict = True  # no extra columns
        coerce = True  # attempt type coercion
```

### Data Quality Checks
Generate assertions for:
- **Completeness** — required fields are non-null
- **Uniqueness** — primary key columns have no duplicates
- **Referential integrity** — foreign keys exist in parent table
- **Range** — numeric values within expected bounds
- **Freshness** — timestamps within expected recency window
- **Row count** — output has expected number of rows (within tolerance)

## Pipeline Structure

For non-trivial pipelines, organize as:
```
pipeline/
├── __init__.py
├── extract.py          # Data source readers
├── transform.py        # Core transformations
├── validate.py         # Schema + quality checks
├── load.py             # Output writers
├── models.py           # Pydantic/pandera schemas
├── config.py           # Source URLs, credentials, params
└── main.py             # Orchestration: extract → validate → transform → validate → load
```

## Performance Rules

- **Lazy evaluation** — use `scan_csv`/`scan_parquet` not `read_csv`/`read_parquet` for large files
- **Column selection** — select only needed columns early (`select` before `filter`)
- **Predicate pushdown** — filter before join
- **Type efficiency** — use categorical for low-cardinality strings, appropriate int sizes
- **Chunk processing** — for files that don't fit in memory, process in batches
- **Parallelism** — polars auto-parallelizes; for pandas, use `swifter` or `pandarallel`

## Output Formats

| Format | When to Use | Library |
|--------|-------------|---------|
| Parquet | Analytics, data warehouse | `polars`, `pyarrow` |
| CSV | Human-readable, small data, interop | `polars` |
| JSON/JSONL | API consumption, streaming | `polars`, `orjson` |
| Database | Persistent storage | `sqlalchemy`, `connectorx` |
| Delta Lake | Versioned, ACID, time travel | `deltalake` |

## Output Format

Write all pipeline code using apply_patch. Include a `main.py` or script entry point that runs the full pipeline. Print sample input → output to demonstrate correctness.
