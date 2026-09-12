---
name: fuzzy-match
description: Approximate string matching using rapidfuzz for typo-tolerant entity resolution. Use when exact matching fails due to spelling variation.
---

# Fuzzy Matching

Use this skill when exact text matching is too strict.

## Workflow

1. Normalize casing and whitespace.
2. Compute similarity scores.
3. Apply an acceptance threshold.
4. Log chosen match and score.

## Practical Thresholds

- `>= 95`: near-exact
- `85-94`: likely same entity
- `< 85`: require secondary validation

## Example

```python
from rapidfuzz import fuzz
score = fuzz.ratio("Al Quds Rest.", "Al Quds Restaurant")
```
