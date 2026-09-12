# Formula Card

## COGS

- `opening_inventory_value = opening_qty * unit_cost`
- `closing_inventory_value = closing_qty * unit_cost`
- `actual_cogs = opening_inventory_value + purchases_value - closing_inventory_value`
- `cogs_pct = actual_cogs / total_revenue`

## Variance

- `theoretical_usage = sum(qty_sold * recipe_qty)`
- `actual_usage = opening_qty + purchase_qty - closing_qty`
- `variance_pct = (actual_usage - theoretical_usage) / theoretical_usage`

## Labor

- `overtime_premium = overtime_hours * hourly_rate * 0.5`
- `row_total_cost = hours * hourly_rate + overtime_premium`
- `daily_labor_pct = daily_labor_cost / daily_revenue`

## Prime Cost

- `prime_cost_value = cogs + labor`
- `prime_cost_pct = prime_cost_value / revenue`
