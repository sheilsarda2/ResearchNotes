#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path

from openpyxl import Workbook

SOURCE_DIR = Path("/tmp/task-data")
TARGET_DIR = Path("/root/data")


def copy_core_files() -> None:
    for name in [
        "pos_sales.csv",
        "recipes.json",
        "inventory_open.csv",
        "inventory_close.csv",
        "historical.csv",
    ]:
        shutil.copy2(SOURCE_DIR / name, TARGET_DIR / name)


def generate_labor_schedule() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "schedule"
    ws.append(["date", "employee", "role", "hours", "hourly_rate"])

    rows = [
        ("2026-03-23", "Nadia", "Chef", 3, 14),
        ("2026-03-23", "Yousef", "Line Cook", 3, 12),
        ("2026-03-23", "Maya", "Server", 2, 10),
        ("2026-03-23", "Rami", "Barista", 2, 11),
        ("2026-03-23", "Sara", "Manager", 6, 14),
        ("2026-03-24", "Nadia", "Chef", 3, 14),
        ("2026-03-24", "Yousef", "Line Cook", 3, 12),
        ("2026-03-24", "Maya", "Server", 2, 10),
        ("2026-03-24", "Rami", "Barista", 2, 11),
        ("2026-03-24", "Sara", "Manager", 6, 14),
        ("2026-03-25", "Nadia", "Chef", 3, 14),
        ("2026-03-25", "Yousef", "Line Cook", 4, 12),
        ("2026-03-25", "Maya", "Server", 3, 10),
        ("2026-03-25", "Rami", "Barista", 2, 11),
        ("2026-03-25", "Sara", "Manager", 6, 14),
        ("2026-03-26", "Nadia", "Chef", 3, 14),
        ("2026-03-26", "Yousef", "Line Cook", 4, 12),
        ("2026-03-26", "Maya", "Server", 3, 10),
        ("2026-03-26", "Rami", "Barista", 3, 11),
        ("2026-03-26", "Sara", "Manager", 6, 14),
        ("2026-03-27", "Nadia", "Chef", 3, 14),
        ("2026-03-27", "Yousef", "Line Cook", 4, 12),
        ("2026-03-27", "Maya", "Server", 3, 10),
        ("2026-03-27", "Rami", "Barista", 3, 11),
        ("2026-03-27", "Sara", "Manager", 6, 14),
        ("2026-03-28", "Nadia", "Chef", 3, 14),
        ("2026-03-28", "Yousef", "Line Cook", 4, 12),
        ("2026-03-28", "Maya", "Server", 4, 10),
        ("2026-03-28", "Rami", "Barista", 3, 11),
        ("2026-03-28", "Sara", "Manager", 6, 14),
        ("2026-03-29", "Nadia", "Chef", 2, 14),
        ("2026-03-29", "Yousef", "Line Cook", 2, 12),
        ("2026-03-29", "Maya", "Server", 3, 10),
        ("2026-03-29", "Rami", "Barista", 3, 11),
        ("2026-03-29", "Sara", "Manager", 5, 14),
    ]

    for row in rows:
        ws.append(row)

    wb.save(TARGET_DIR / "labor_schedule.xlsx")


def copy_invoice_pdfs() -> None:
    source_invoice_dir = SOURCE_DIR / "invoices"
    target_invoice_dir = TARGET_DIR / "invoices"
    target_invoice_dir.mkdir(parents=True, exist_ok=True)
    for pdf_path in sorted(source_invoice_dir.glob("*.pdf")):
        shutil.copy2(pdf_path, target_invoice_dir / pdf_path.name)


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    copy_core_files()
    generate_labor_schedule()
    copy_invoice_pdfs()


if __name__ == "__main__":
    main()
