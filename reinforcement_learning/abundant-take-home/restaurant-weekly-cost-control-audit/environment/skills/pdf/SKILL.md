---
name: pdf
description: Extract structured invoice rows from PDF files using pdfplumber. Use when tasks require parsing tabular text across one or more PDF pages and reconciling totals.
---

# PDF Processing

Use this skill when source data is in PDF invoices and you need deterministic rows for calculations.

## Workflow

1. Open each PDF with `pdfplumber`.
2. Iterate all pages in order.
3. Extract text from each page.
4. Keep only row-like lines (for this task, pipe-delimited lines).
5. Skip header lines.
6. Cast numeric fields immediately.
7. Validate parsed totals.

## Extraction Pattern

Expected normalized row shape in this task:
`ingredient | category | purchase_qty | unit | purchase_unit_cost | purchases_value`

- Reject rows that do not have exactly 6 fields.
- Reject rows where first column equals `Item`.
- Preserve ingredient and category labels exactly.

## Validation

- Confirm all expected invoice PDFs were parsed.
- Confirm each file produced non-empty rows.
- Confirm sum of line totals equals purchase aggregation.

## Reusable Script

Use `scripts/extract_invoice_rows.py` when you need a ready-to-run parser.
It returns dictionaries with these numeric keys:
`purchase_qty`, `purchase_unit_cost`, and `purchases_value`.
