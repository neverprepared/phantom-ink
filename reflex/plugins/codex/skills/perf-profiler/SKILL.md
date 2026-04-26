---
name: perf-profiler
description: Analyze performance data — flame graphs, benchmarks, profiler output, slow queries, and runtime metrics. Use when the user wants to identify bottlenecks, optimize hot paths, reduce memory usage, or improve query performance.
---

# Performance Profiler

You analyze performance data and produce actionable optimization recommendations. Given profiler output, benchmarks, slow queries, or metric dashboards, identify the bottleneck and generate the fix.

## Execution Model

1. Collect or read the performance data
2. Identify the top bottlenecks (Pareto: focus on the 20% causing 80% of impact)
3. Explain why each is slow with specific numbers
4. Generate the optimized code
5. Provide a benchmark to prove the improvement

## Profiling Commands

### CPU Profiling

**Go**
```bash
# Generate CPU profile
go test -bench=. -cpuprofile=cpu.prof
# Or runtime profiling
import _ "net/http/pprof"  # then GET /debug/pprof/profile?seconds=30
# Analyze
go tool pprof -http=:8080 cpu.prof
```

**Python**
```bash
# cProfile
python -m cProfile -o profile.prof script.py
python -m snakeviz profile.prof  # visualize
# py-spy (sampling, no code changes)
py-spy record -o profile.svg -- python script.py
# scalene (CPU + memory + GPU)
scalene script.py
```

**Node.js**
```bash
# V8 profiler
node --prof app.js
node --prof-process isolate-*.log > profile.txt
# clinic.js
npx clinic flame -- node app.js
```

### Memory Profiling

**Go**
```bash
go test -bench=. -memprofile=mem.prof
go tool pprof -http=:8080 mem.prof
# Runtime
runtime.ReadMemStats(&m)
```

**Python**
```bash
# memory_profiler
python -m memory_profiler script.py
# tracemalloc (stdlib)
tracemalloc.start()
snapshot = tracemalloc.take_snapshot()
```

**Node.js**
```bash
node --inspect app.js  # Chrome DevTools heap snapshot
# Or programmatic
const v8 = require('v8');
v8.writeHeapSnapshot();
```

### Database Query Profiling

**PostgreSQL**
```sql
-- Enable query stats
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
-- Top slow queries
SELECT query, mean_exec_time, calls, total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
-- Explain a specific query
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT ...;
```

**MySQL**
```sql
SET profiling = 1;
SELECT ...;
SHOW PROFILE FOR QUERY 1;
-- Slow query log
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 0.5;
```

## Common Bottleneck Patterns

### N+1 Queries
**Symptom**: Linear query count with result set size
**Detection**: Query log shows repeated identical queries with different IDs
**Fix**: Batch load with `WHERE id IN (...)` or ORM eager loading
```python
# Bad: N+1
for user in users:
    orders = db.query(Order).filter(Order.user_id == user.id).all()

# Good: Eager load
users = db.query(User).options(joinedload(User.orders)).all()
```

### Missing Index
**Symptom**: Sequential scan on filtered/joined column
**Detection**: `EXPLAIN` shows `Seq Scan` with large `rows` count
**Fix**: Add targeted index
```sql
-- Before: Seq Scan on orders (cost=0.00..12345.00 rows=500000)
-- After:  Index Scan using idx_orders_user_id (cost=0.42..8.44 rows=5)
CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders (user_id);
```

### Unbounded Memory Growth
**Symptom**: RSS grows over time, never releases
**Detection**: Heap profile shows growing allocations in a specific call site
**Fix**: Bound buffers, use streaming, add pooling
```go
// Bad: unbounded buffer
var results []Result
for rows.Next() {
    results = append(results, scan(rows))
}

// Good: streaming with channel
func streamResults(ctx context.Context) <-chan Result {
    ch := make(chan Result, 100)
    go func() {
        defer close(ch)
        for rows.Next() {
            select {
            case ch <- scan(rows):
            case <-ctx.Done():
                return
            }
        }
    }()
    return ch
}
```

### Hot Loop Allocation
**Symptom**: GC pressure, high allocation rate in tight loop
**Detection**: Memory profile shows allocations inside loop body
**Fix**: Hoist allocations, use sync.Pool, pre-allocate slices
```go
// Bad: allocate in loop
for _, item := range items {
    buf := make([]byte, 4096)
    process(item, buf)
}

// Good: reuse buffer
buf := make([]byte, 4096)
for _, item := range items {
    process(item, buf)
}
```

### Serialization Overhead
**Symptom**: CPU time dominated by JSON/XML marshal/unmarshal
**Detection**: Flame graph shows encoding/json or similar
**Fix**: Use faster serializer, code-generated marshaling, or binary format
```go
// Switch encoding/json → github.com/goccy/go-json
// Or use code generation: github.com/mailru/easyjson
```

### Connection Exhaustion
**Symptom**: Latency spikes, "too many connections" errors
**Detection**: Connection pool metrics show max utilization
**Fix**: Pool tuning, connection reuse, timeouts
```go
db.SetMaxOpenConns(25)
db.SetMaxIdleConns(10)
db.SetConnMaxLifetime(5 * time.Minute)
db.SetConnMaxIdleTime(1 * time.Minute)
```

## Benchmark Template

Always provide a before/after benchmark:

### Go
```go
func BenchmarkOptimized(b *testing.B) {
    // setup
    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        optimizedFunction(input)
    }
}
```

### Python
```python
import timeit

before = timeit.timeit(lambda: slow_function(data), number=1000)
after = timeit.timeit(lambda: fast_function(data), number=1000)
print(f"Before: {before:.3f}s | After: {after:.3f}s | Speedup: {before/after:.1f}x")
```

### Node.js
```javascript
import { bench, run } from 'mitata';

bench('before', () => slowFunction(data));
bench('after', () => fastFunction(data));
await run();
```

## Report Format

```markdown
## Performance Analysis

### Top Bottlenecks
1. **N+1 query in UserService.getAll()** — 500 queries per request (should be 2)
   - Impact: 340ms → 12ms (28x speedup)
   - Fix: Eager load associations
   
2. **Missing index on orders.created_at** — full table scan on 2M rows
   - Impact: 1.2s → 3ms (400x speedup)
   - Fix: CREATE INDEX CONCURRENTLY

3. **JSON serialization in hot path** — 40% of CPU in encoding/json
   - Impact: 15ms → 3ms per request (5x speedup)
   - Fix: Switch to go-json with code generation

### Recommendations
- [ ] Fix #1 — highest impact, lowest risk
- [ ] Fix #2 — requires migration, schedule in maintenance window
- [ ] Fix #3 — requires dependency change, test thoroughly
```

## Rules

- **Numbers, not opinions** — every claim needs a measurement
- **Before AND after** — never claim improvement without proving it
- **One change at a time** — isolate optimizations to measure independently
- **Profile first** — never guess at bottlenecks, always measure
- **Consider trade-offs** — faster code may use more memory, be less readable, or be harder to maintain
