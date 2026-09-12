---
name: xlsx
description: Build deterministic multi-sheet Excel outputs with openpyxl. Use when tasks require exact sheet names, typed numeric cells, and verifier-friendly workbook layouts.
---

# XLSX Reporting

Use this skill for writing the final workbook output.

## Core Rules

- Use exact sheet names required by the task.
- Keep numeric values as numbers, not strings.
- Keep row order deterministic.
- Keep headers explicit and stable.
- Avoid merged cells in verifier-read regions.

## Build Pattern

1. Create workbook and primary sheet.
2. Create remaining sheets by exact name.
3. Write one header row per table section.
4. Append typed rows.
5. Save once to final path.

## Numeric Guidance

- Store percentages as decimal numbers (example `0.35325`).
- Round only where stable precision is needed.
- Do not write formatted display text in numeric columns.

## Reusable Script

Use `scripts/write_table.py` to append header + rows consistently.
