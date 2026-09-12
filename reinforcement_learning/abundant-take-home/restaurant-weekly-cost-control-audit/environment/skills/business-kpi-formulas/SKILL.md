---
name: business-kpi-formulas
description: KPI formulas for restaurant cost-control reporting. Use when calculating COGS, usage variance, labor ratios, prime cost, thresholds, and action labels in deterministic audits.
---

# Business KPI Formulas

Use this skill for deterministic weekly operations audits.
Keep formulas fixed, and read policy values (thresholds, labels, weekend days) from the task brief or config source.

## Runtime Parameters

Set these from the task instructions instead of hardcoding them in the skill:

- category COGS thresholds by category
- variance alert threshold
- labor alert threshold
- action-label bands for prime cost
- weekend days for the business locale

## COGS

- `opening_inventory_value = opening_qty * unit_cost`
- `closing_inventory_value = closing_qty * unit_cost`
- `actual_cogs = opening_inventory_value + purchases_value - closing_inventory_value`
- `cogs_pct_of_revenue = actual_cogs / total_revenue`
- `cogs_flag = flag_label` when `cogs_pct_of_revenue > category_threshold`, else blank

## Theoretical vs Actual Usage

- `theoretical_usage_qty = sum(qty_sold * recipe_qty)`
- `actual_usage_qty = opening_qty + purchase_qty - closing_qty`
- `variance_pct = (actual_usage_qty - theoretical_usage_qty) / theoretical_usage_qty`
- `variance_flag = variance_flag_label` when `abs(variance_pct) > variance_threshold`, else blank

## Labor

- `overtime_premium = overtime_hours * hourly_rate * 0.5`
- `daily_labor_cost = sum(hours * hourly_rate + overtime_premium)`
- `labor_pct_of_revenue = daily_labor_cost / daily_revenue`
- `labor_flag = labor_flag_label` when `labor_pct_of_revenue > labor_threshold`, else blank
- `is_weekend` comes from configured locale weekend days (for example Fri/Sat or Sat/Sun)

## Prime Cost

- `prime_cost = cogs + labor`
- `prime_cost_pct = prime_cost / revenue`
- `recommended_action` is assigned from configured action bands (low/medium/high ranges)

See `references/formulas.md` for compact formula card.
