#!/bin/bash
set -e

python3 <<'PYTHON_SCRIPT'
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pdfplumber
from openpyxl import Workbook

DATA_DIR = Path("/root/data")
OUTPUT_FILE = Path("/root/audit_report.xlsx")


def parse_invoice_pdf(pdf_path: Path) -> list[dict]:
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
                        "purchase_qty": float(parts[2]),
                        "unit": parts[3],
                        "purchase_unit_cost": float(parts[4]),
                        "purchases_value": float(parts[5]),
                    }
                )
    return rows


def load_purchases(invoices_dir: Path) -> pd.DataFrame:
    invoice_rows: list[dict] = []
    for pdf_path in sorted(invoices_dir.glob("*.pdf")):
        invoice_rows.extend(parse_invoice_pdf(pdf_path))
    return pd.DataFrame(invoice_rows)


def compute_theoretical_usage(pos_df: pd.DataFrame, recipes: dict) -> pd.DataFrame:
    usage: dict[str, float] = {}
    for _, row in pos_df.iterrows():
        item = row["menu_item"]
        qty_sold = float(row["qty_sold"])
        for ingredient, qty_per_item in recipes[item].items():
            usage[ingredient] = usage.get(ingredient, 0.0) + qty_sold * float(qty_per_item)
    return pd.DataFrame(
        [{"ingredient": ingredient, "theoretical_usage_qty": qty} for ingredient, qty in usage.items()]
    )


def compute_labor_tables(pos_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    labor_df = pd.read_excel(DATA_DIR / "labor_schedule.xlsx", sheet_name="schedule").copy()
    labor_df = labor_df.assign(date=pd.to_datetime(labor_df["date"]))
    labor_df = labor_df.sort_values(["employee", "date"]).reset_index(drop=True)

    overtime_hours = [0.0] * len(labor_df)
    overtime_premium = [0.0] * len(labor_df)
    row_total_cost = [0.0] * len(labor_df)

    for employee, group in labor_df.groupby("employee"):
        cumulative_hours = 0.0
        for idx in group.index:
            hours = float(labor_df.at[idx, "hours"])
            rate = float(labor_df.at[idx, "hourly_rate"])
            regular_hours = max(min(hours, 40.0 - cumulative_hours), 0.0)
            overtime = max(hours - regular_hours, 0.0)
            premium = overtime * rate * 0.5

            overtime_hours[idx] = overtime
            overtime_premium[idx] = premium
            row_total_cost[idx] = hours * rate + premium

            cumulative_hours += hours

    labor_df.loc[:, "overtime_hours"] = overtime_hours
    labor_df.loc[:, "overtime_premium"] = overtime_premium
    labor_df.loc[:, "row_total_cost"] = row_total_cost

    daily_labor = (
        labor_df.groupby("date", as_index=False)
        .agg(total_hours=("hours", "sum"), daily_labor_cost=("row_total_cost", "sum"))
        .sort_values("date")
    )

    daily_revenue = (
        pos_df.groupby("date", as_index=False)["gross_revenue"].sum().rename(columns={"gross_revenue": "daily_revenue"})
    ).copy()
    daily_revenue = daily_revenue.assign(date=pd.to_datetime(daily_revenue["date"]))

    daily_labor = daily_labor.merge(daily_revenue, on="date", how="left").copy()
    daily_labor.loc[:, "labor_pct_of_revenue"] = daily_labor["daily_labor_cost"] / daily_labor["daily_revenue"]
    daily_labor.loc[:, "flag"] = daily_labor["labor_pct_of_revenue"].apply(lambda x: "ALERT" if x > 0.35 else "")
    # Jordan weekend convention: Friday (4) and Saturday (5).
    daily_labor.loc[:, "is_weekend"] = daily_labor["date"].dt.dayofweek.apply(
        lambda d: "yes" if int(d) in (4, 5) else "no"
    )
    daily_labor = daily_labor.assign(date=daily_labor["date"].dt.strftime("%Y-%m-%d"))

    overtime_summary = (
        labor_df.groupby("employee", as_index=False)
        .agg(
            total_hours=("hours", "sum"),
            overtime_hours=("overtime_hours", "sum"),
            overtime_premium=("overtime_premium", "sum"),
        )
        .sort_values("employee")
    )

    return daily_labor, overtime_summary


def action_label(pct: float) -> str:
    if pct > 0.65:
        return "Immediate Action"
    if pct > 0.60:
        return "Monitor"
    return "On Track"


def main() -> None:
    pos_df = pd.read_csv(DATA_DIR / "pos_sales.csv")
    with open(DATA_DIR / "recipes.json", encoding="utf-8") as f:
        recipes = json.load(f)

    inventory_open = pd.read_csv(DATA_DIR / "inventory_open.csv")
    inventory_close = pd.read_csv(DATA_DIR / "inventory_close.csv")
    purchases = load_purchases(DATA_DIR / "invoices")

    purchases_agg = (
        purchases.groupby(["ingredient", "category", "unit"], as_index=False)
        .agg(
            purchase_qty=("purchase_qty", "sum"),
            purchases_value=("purchases_value", "sum"),
        )
        .sort_values("ingredient")
    )

    usage_df = inventory_open.merge(
        inventory_close[["ingredient", "closing_qty"]],
        on="ingredient",
        how="left",
    ).merge(
        purchases_agg[["ingredient", "purchase_qty", "purchases_value"]],
        on="ingredient",
        how="left",
    ).copy()
    usage_df.loc[:, "purchase_qty"] = usage_df["purchase_qty"].fillna(0.0)
    usage_df.loc[:, "purchases_value"] = usage_df["purchases_value"].fillna(0.0)
    usage_df.loc[:, "actual_usage_qty"] = usage_df["opening_qty"] + usage_df["purchase_qty"] - usage_df["closing_qty"]
    usage_df.loc[:, "opening_inventory_value"] = usage_df["opening_qty"] * usage_df["unit_cost"]
    usage_df.loc[:, "closing_inventory_value"] = usage_df["closing_qty"] * usage_df["unit_cost"]

    total_revenue = float(pos_df["gross_revenue"].sum())

    category_order = ["food", "beverage", "dry goods"]
    cogs_df = (
        usage_df.groupby("category", as_index=False)
        .agg(
            opening_inventory_value=("opening_inventory_value", "sum"),
            purchases_value=("purchases_value", "sum"),
            closing_inventory_value=("closing_inventory_value", "sum"),
        )
        .set_index("category")
        .reindex(category_order)
        .reset_index()
    ).copy()
    cogs_df.loc[:, "actual_cogs"] = (
        cogs_df["opening_inventory_value"] + cogs_df["purchases_value"] - cogs_df["closing_inventory_value"]
    )
    cogs_df.loc[:, "cogs_pct_of_revenue"] = cogs_df["actual_cogs"] / total_revenue

    threshold_by_category = {"food": 0.32, "beverage": 0.25, "dry goods": 0.15}
    cogs_df.loc[:, "threshold_pct"] = cogs_df["category"].map(threshold_by_category)
    cogs_df.loc[:, "flag"] = cogs_df.apply(
        lambda r: "ALERT" if float(r["cogs_pct_of_revenue"]) > float(r["threshold_pct"]) else "",
        axis=1,
    )

    theoretical_df = compute_theoretical_usage(pos_df, recipes)
    variance_df = theoretical_df.merge(
        usage_df[["ingredient", "category", "actual_usage_qty", "unit_cost"]],
        on="ingredient",
        how="left",
    ).sort_values("ingredient").copy()
    variance_df.loc[:, "variance_pct"] = (
        variance_df["actual_usage_qty"] - variance_df["theoretical_usage_qty"]
    ) / variance_df["theoretical_usage_qty"]
    variance_df.loc[:, "flag"] = variance_df["variance_pct"].apply(
        lambda x: "investigate" if abs(float(x)) > 0.08 else ""
    )
    variance_df.loc[:, "variance_cost_impact"] = (
        variance_df["actual_usage_qty"] - variance_df["theoretical_usage_qty"]
    ).abs() * variance_df["unit_cost"]

    daily_labor_df, overtime_df = compute_labor_tables(pos_df)

    weekly_cogs = float(cogs_df["actual_cogs"].sum())
    weekly_labor = float(daily_labor_df["daily_labor_cost"].sum())

    prime_daily = daily_labor_df[["date", "daily_revenue", "daily_labor_cost"]].copy()
    prime_daily.loc[:, "allocated_cogs"] = weekly_cogs * (prime_daily["daily_revenue"] / total_revenue)
    prime_daily.loc[:, "prime_cost"] = prime_daily["allocated_cogs"] + prime_daily["daily_labor_cost"]
    prime_daily.loc[:, "prime_cost_pct"] = prime_daily["prime_cost"] / prime_daily["daily_revenue"]
    prime_daily.loc[:, "recommended_action"] = prime_daily["prime_cost_pct"].apply(action_label)

    weekly_prime_pct = (weekly_cogs + weekly_labor) / total_revenue
    weekly_average_daily_prime_pct = float(prime_daily["prime_cost_pct"].mean())
    weekly_recommended_action = action_label(weekly_prime_pct)

    historical_df = pd.read_csv(DATA_DIR / "historical.csv").copy()
    historical_df = historical_df.assign(week_start=pd.to_datetime(historical_df["week_start"]))
    last_week_prime_pct = float(historical_df.sort_values("week_start")["prime_cost_pct"].iloc[-1])
    week_over_week_change = weekly_prime_pct - last_week_prime_pct

    current_week_start = pd.to_datetime(pos_df["date"]).min().strftime("%Y-%m-%d")
    trend_df = historical_df.copy()
    trend_df = trend_df.assign(week_start=trend_df["week_start"].dt.strftime("%Y-%m-%d"))
    trend_df = pd.concat(
        [
            trend_df,
            pd.DataFrame([{"week_start": current_week_start, "prime_cost_pct": weekly_prime_pct}]),
        ],
        ignore_index=True,
    )

    concerns: list[dict] = []

    for _, row in cogs_df.iterrows():
        overrun = float(row["cogs_pct_of_revenue"]) - float(row["threshold_pct"])
        if overrun > 0:
            concerns.append(
                {
                    "concern": f"{row['category']} COGS above threshold",
                    "financial_impact": overrun * total_revenue,
                }
            )

    for _, row in variance_df.iterrows():
        if row["flag"]:
            concerns.append(
                {
                    "concern": f"{row['ingredient']} variance above 8%",
                    "financial_impact": float(row["variance_cost_impact"]),
                }
            )

    for _, row in daily_labor_df.iterrows():
        overrun = float(row["daily_labor_cost"]) - 0.35 * float(row["daily_revenue"])
        if overrun > 0:
            concerns.append(
                {
                    "concern": f"Labor over 35% on {row['date']}",
                    "financial_impact": overrun,
                }
            )

    prime_overrun = weekly_prime_pct - 0.65
    if prime_overrun > 0:
        concerns.append(
            {
                "concern": "Weekly prime cost above 65%",
                "financial_impact": prime_overrun * total_revenue,
            }
        )

    top_concerns = sorted(concerns, key=lambda x: x["financial_impact"], reverse=True)[:3]

    if weekly_prime_pct > 0.65:
        traffic_light = "Red"
    elif weekly_prime_pct > 0.60:
        traffic_light = "Yellow"
    else:
        traffic_light = "Green"

    wb = Workbook()

    ws_cogs = wb.active
    ws_cogs.title = "COGS Calculation"
    ws_cogs.append(
        [
            "category",
            "opening_inventory_value",
            "purchases_value",
            "closing_inventory_value",
            "actual_cogs",
            "cogs_pct_of_revenue",
            "threshold_pct",
            "flag",
        ]
    )
    for _, row in cogs_df.iterrows():
        ws_cogs.append(
            [
                row["category"],
                round(float(row["opening_inventory_value"]), 2),
                round(float(row["purchases_value"]), 2),
                round(float(row["closing_inventory_value"]), 2),
                round(float(row["actual_cogs"]), 2),
                round(float(row["cogs_pct_of_revenue"]), 6),
                round(float(row["threshold_pct"]), 6),
                row["flag"],
            ]
        )

    ws_var = wb.create_sheet("Theoretical vs Actual Variance")
    ws_var.append(
        [
            "ingredient",
            "category",
            "theoretical_usage_qty",
            "actual_usage_qty",
            "variance_pct",
            "flag",
            "variance_cost_impact",
        ]
    )
    for _, row in variance_df.iterrows():
        ws_var.append(
            [
                row["ingredient"],
                row["category"],
                round(float(row["theoretical_usage_qty"]), 4),
                round(float(row["actual_usage_qty"]), 4),
                round(float(row["variance_pct"]), 6),
                row["flag"],
                round(float(row["variance_cost_impact"]), 2),
            ]
        )

    ws_labor = wb.create_sheet("Labor Analysis")
    ws_labor.append(["date", "is_weekend", "total_hours", "labor_cost", "labor_pct_of_revenue", "flag"])
    for _, row in daily_labor_df.iterrows():
        ws_labor.append(
            [
                row["date"],
                row["is_weekend"],
                round(float(row["total_hours"]), 2),
                round(float(row["daily_labor_cost"]), 2),
                round(float(row["labor_pct_of_revenue"]), 6),
                row["flag"],
            ]
        )

    ws_labor.append([])
    ws_labor.append(["employee", "total_hours", "overtime_hours", "overtime_premium"])
    for _, row in overtime_df.iterrows():
        ws_labor.append(
            [
                row["employee"],
                round(float(row["total_hours"]), 2),
                round(float(row["overtime_hours"]), 2),
                round(float(row["overtime_premium"]), 2),
            ]
        )

    ws_prime = wb.create_sheet("Prime Cost Summary")
    ws_prime.append(
        [
            "date",
            "daily_revenue",
            "allocated_cogs",
            "labor_cost",
            "prime_cost",
            "prime_cost_pct",
            "recommended_action",
        ]
    )
    for _, row in prime_daily.iterrows():
        ws_prime.append(
            [
                row["date"],
                round(float(row["daily_revenue"]), 2),
                round(float(row["allocated_cogs"]), 2),
                round(float(row["daily_labor_cost"]), 2),
                round(float(row["prime_cost"]), 2),
                round(float(row["prime_cost_pct"]), 6),
                row["recommended_action"],
            ]
        )

    ws_prime.append([])
    ws_prime.append(["metric", "value"])
    ws_prime.append(["weekly_prime_cost_pct", round(float(weekly_prime_pct), 6)])
    ws_prime.append(["weekly_average_daily_prime_pct", round(float(weekly_average_daily_prime_pct), 6)])
    ws_prime.append(["weekly_recommended_action", weekly_recommended_action])

    ws_prime.append([])
    ws_prime.append(["week_start", "prime_cost_pct"])
    for _, row in trend_df.iterrows():
        ws_prime.append([row["week_start"], round(float(row["prime_cost_pct"]), 6)])

    ws_exec = wb.create_sheet("Executive Summary")
    ws_exec["A1"] = "overall_health"
    ws_exec["B1"] = traffic_light
    ws_exec["A2"] = "week_over_week_prime_cost_change_pct"
    ws_exec["B2"] = round(float(week_over_week_change), 6)
    ws_exec["A4"] = "rank"
    ws_exec["B4"] = "concern"
    ws_exec["C4"] = "financial_impact"

    rank = 1
    for concern in top_concerns:
        ws_exec.append(
            [
                rank,
                concern["concern"],
                round(float(concern["financial_impact"]), 2),
            ]
        )
        rank += 1

    wb.save(OUTPUT_FILE)


if __name__ == "__main__":
    main()
PYTHON_SCRIPT
