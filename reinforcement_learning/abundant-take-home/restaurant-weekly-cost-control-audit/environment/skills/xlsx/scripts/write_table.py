#!/usr/bin/env python3
from __future__ import annotations

from typing import Iterable

from openpyxl import Workbook


def write_table(
    workbook: Workbook,
    sheet_name: str,
    headers: list[str],
    rows: Iterable[Iterable],
) -> None:
    if sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
    else:
        ws = workbook.create_sheet(sheet_name)

    ws.append(headers)
    for row in rows:
        ws.append(list(row))
