---
name: sql-window
description: SQL window-function patterns for ranking, running totals, and period comparisons. Use when reports require partitioned analytical calculations.
---

# SQL Window Functions

Use this skill for analytical SQL computations.

## Common Patterns

- `row_number() over (partition by ... order by ...)`
- `sum(x) over (partition by ... order by ... rows between unbounded preceding and current row)`
- `lag()` and `lead()`

## Guidance

- Always define deterministic ordering columns.
- Keep partitioning keys aligned with business grouping.
- Validate null handling for lag/lead operations.
