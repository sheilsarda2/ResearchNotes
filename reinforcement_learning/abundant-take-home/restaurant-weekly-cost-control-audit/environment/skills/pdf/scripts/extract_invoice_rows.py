#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import pdfplumber


def _to_float(value: str) -> float:
    return float(value.replace(",", "").replace("$", "").strip())


def extract_invoice_rows(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                if "|" not in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) != 6 or parts[0].lower() == "item":
                    continue
                rows.append(
                    {
                        "ingredient": parts[0],
                        "category": parts[1],
                        "purchase_qty": _to_float(parts[2]),
                        "unit": parts[3],
                        "purchase_unit_cost": _to_float(parts[4]),
                        "purchases_value": _to_float(parts[5]),
                    }
                )
    return rows


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: extract_invoice_rows.py <invoice.pdf>")

    path = Path(sys.argv[1])
    print(json.dumps(extract_invoice_rows(path), indent=2))
