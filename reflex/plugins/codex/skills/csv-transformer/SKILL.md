---
name: csv-transformer
description: Parse, transform, join, reshape, and analyze CSV, JSON, and tabular data files. Use when the user needs to clean data, merge files, compute aggregations, pivot tables, generate reports, or convert between data formats.
---

# CSV Transformer

You process tabular data — CSV, TSV, JSON, JSONL, Parquet, Excel. Given input files and a desired transformation, produce the output data or the script to generate it.

## Execution Model

1. Read and inspect the input data (schema, row count, sample rows)
2. Understand the desired transformation
3. Generate the transformation script (prefer DuckDB for SQL, polars for Python)
4. Execute or provide the runnable script
5. Show sample output (first 10 rows)

## Tool Selection

| Task | Best Tool | Why |
|------|-----------|-----|
| Ad-hoc queries on files | **DuckDB** | SQL on files, no setup, fast |
| Complex transforms | **polars** | Lazy eval, fast, expressive |
| Quick one-liners | **awk/jq/Miller** | No dependencies, scriptable |
| Excel with formulas | **openpyxl** | Preserves formatting |
| Large file streaming | **polars lazy / DuckDB** | Memory efficient |

## DuckDB (Preferred for SQL on Files)

```sql
-- Read CSV directly
SELECT * FROM 'data.csv' LIMIT 10;

-- Schema detection
DESCRIBE SELECT * FROM 'data.csv';

-- Aggregation
SELECT
    department,
    COUNT(*) AS headcount,
    ROUND(AVG(salary), 2) AS avg_salary,
    MAX(salary) AS max_salary
FROM 'employees.csv'
GROUP BY department
ORDER BY avg_salary DESC;

-- Join files
SELECT o.*, c.name AS customer_name, c.email
FROM 'orders.csv' o
JOIN 'customers.csv' c ON o.customer_id = c.id;

-- Pivot
PIVOT (SELECT department, quarter, revenue FROM 'sales.csv')
ON quarter
USING SUM(revenue);

-- Write output
COPY (SELECT ... FROM 'input.csv') TO 'output.csv' (HEADER, DELIMITER ',');
COPY (SELECT ... FROM 'input.csv') TO 'output.parquet' (FORMAT PARQUET);
COPY (SELECT ... FROM 'input.csv') TO 'output.json' (FORMAT JSON, ARRAY true);

-- Read multiple files
SELECT * FROM 'data/*.csv';
SELECT * FROM read_csv_auto('data/*.csv', filename=true);
```

### Running DuckDB
```bash
# Interactive
duckdb
# One-liner
duckdb -c "SELECT count(*) FROM 'data.csv'"
# From script
duckdb < transform.sql
# Python
import duckdb
df = duckdb.sql("SELECT * FROM 'data.csv' WHERE amount > 100").pl()
```

## Polars (Complex Transforms)

```python
import polars as pl

# Read
df = pl.read_csv("input.csv")
df = pl.scan_csv("large_input.csv")  # lazy, for large files

# Clean
df = df.with_columns(
    pl.col("email").str.to_lowercase().str.strip_chars(),
    pl.col("name").str.strip_chars().str.to_titlecase(),
    pl.col("date").str.to_date("%Y-%m-%d"),
    pl.col("amount").cast(pl.Decimal(10, 2)),
)

# Filter
df = df.filter(
    (pl.col("status") == "active") &
    (pl.col("amount") > 0) &
    pl.col("email").is_not_null()
)

# Deduplicate
df = df.unique(subset=["email"], keep="last")

# Aggregate
summary = df.group_by("category").agg(
    pl.col("amount").sum().alias("total"),
    pl.col("amount").mean().alias("average"),
    pl.len().alias("count"),
)

# Pivot
pivoted = df.pivot(
    on="quarter",
    index="product",
    values="revenue",
    aggregate_function="sum",
)

# Unpivot (melt)
melted = df.unpivot(
    on=["q1", "q2", "q3", "q4"],
    index="product",
    variable_name="quarter",
    value_name="revenue",
)

# Window functions
df = df.with_columns(
    pl.col("amount")
        .rank(method="dense", descending=True)
        .over("category")
        .alias("rank_in_category"),
    pl.col("amount")
        .sum()
        .over("category")
        .alias("category_total"),
)

# Write
df.write_csv("output.csv")
df.write_parquet("output.parquet")
df.write_json("output.json", row_oriented=True)
df.write_excel("output.xlsx")
```

## Common Operations

### Merge/Join Files
```bash
# DuckDB
duckdb -c "
  SELECT a.*, b.extra_field
  FROM 'file_a.csv' a
  LEFT JOIN 'file_b.csv' b ON a.id = b.id
" -csv > merged.csv
```

### Deduplicate
```bash
duckdb -c "
  SELECT DISTINCT ON (email) *
  FROM 'data.csv'
  ORDER BY email, updated_at DESC
" -csv > deduped.csv
```

### Fill Missing Values
```python
df = df.with_columns(
    pl.col("name").fill_null("Unknown"),
    pl.col("amount").fill_null(0),
    pl.col("date").fill_null(pl.col("date").forward_fill()),
)
```

### Split File
```python
for name, group in df.group_by("category"):
    group.write_csv(f"output/{name[0]}.csv")
```

### Convert Between Formats
```bash
# CSV → Parquet
duckdb -c "COPY (SELECT * FROM 'data.csv') TO 'data.parquet' (FORMAT PARQUET)"
# JSON → CSV
duckdb -c "COPY (SELECT * FROM 'data.json') TO 'data.csv' (HEADER)"
# Excel → CSV
duckdb -c "COPY (SELECT * FROM st_read('data.xlsx')) TO 'data.csv' (HEADER)"
```

### Data Quality Report
```sql
SELECT
    column_name,
    COUNT(*) AS total_rows,
    COUNT(column_name) AS non_null,
    COUNT(*) - COUNT(column_name) AS null_count,
    ROUND(100.0 * (COUNT(*) - COUNT(column_name)) / COUNT(*), 1) AS null_pct,
    COUNT(DISTINCT column_name) AS unique_values,
    MIN(column_name) AS min_val,
    MAX(column_name) AS max_val
FROM 'data.csv'
-- repeat per column with UNPIVOT or manual UNION ALL
```

## CLI One-Liners

### jq (JSON)
```bash
# Extract field from JSON array
cat data.json | jq '.[].name'
# Filter
cat data.json | jq '[.[] | select(.status == "active")]'
# Reshape
cat data.json | jq '[.[] | {id, full_name: "\(.first) \(.last)"}]'
```

### Miller (CSV/JSON/TSV)
```bash
# Sort
mlr --csv sort-by -nr amount data.csv
# Filter
mlr --csv filter '$amount > 100' data.csv
# Stats
mlr --csv stats1 -a mean,min,max -f amount -g category data.csv
# Convert
mlr --icsv --ojson cat data.csv
```

### awk
```bash
# Sum a column
awk -F, 'NR>1 {sum+=$3} END {print sum}' data.csv
# Filter rows
awk -F, '$4 == "active"' data.csv
```

## Rules

- **Inspect before transforming** — always show schema and sample rows first
- **Preserve column order** — unless reordering is the goal
- **Handle encodings** — detect and specify UTF-8, Latin-1, etc.
- **Quote handling** — use proper CSV quoting for fields with commas
- **Large files** — use lazy evaluation / streaming for files > 100MB
- **Validate output** — row count check (input rows = output rows for 1:1 transforms)

## Output Format

Print the transformation script and sample output (first 10 rows). If the user wants the output file, write it using apply_patch or print the command to generate it.
