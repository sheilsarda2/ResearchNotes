I need the weekly cost control workbook for our Amman restaurant group. Save it to /root/audit_report.xlsx.

Reporting week is March 23 to 29, 2026, Monday through Sunday. We follow Jordan's weekend schedule, so Friday March 27 and Saturday March 28 are the weekend days this week.

All the input files are under /root/data:
- POS sales: pos_sales.csv
- Vendor invoices: invoices/vendor_sysco.pdf, invoices/vendor_local.pdf, invoices/vendor_beverage.pdf
- Recipes: recipes.json
- Inventory: inventory_open.csv and inventory_close.csv
- Labor: labor_schedule.xlsx
- Historical weekly data: historical.csv

The workbook needs five sheets. The names matter because our automated checks depend on them:
- COGS Calculation
- Theoretical vs Actual Variance
- Labor Analysis
- Prime Cost Summary
- Executive Summary

One formatting note because our QA checker is strict: please keep the column labels exactly as listed below.

For COGS Calculation, use one table starting at row 1 with this header row:
category, opening_inventory_value, purchases_value, closing_inventory_value, actual_cogs, cogs_pct_of_revenue, threshold_pct, flag

For Theoretical vs Actual Variance, use one table starting at row 1 with this header row:
ingredient, category, theoretical_usage_qty, actual_usage_qty, variance_pct, flag, variance_cost_impact

For Labor Analysis, use two sections.
First section starts at row 1 for the daily table with this header row:
date, is_weekend, total_hours, labor_cost, labor_pct_of_revenue, flag
After that table, add a blank row, then the overtime section header row exactly as:
employee, total_hours, overtime_hours, overtime_premium

For Prime Cost Summary, use three sections.
First section header row for the daily table:
date, daily_revenue, allocated_cogs, labor_cost, prime_cost, prime_cost_pct, recommended_action
Then add a blank row and a metric summary section with header:
metric, value
In that metric section, include rows named exactly:
weekly_prime_cost_pct, weekly_average_daily_prime_pct, weekly_recommended_action
Then add a blank row and the trend section with header:
week_start, prime_cost_pct

For Executive Summary, keep this layout:
A1 = overall_health, B1 = Green or Yellow or Red
A2 = week_over_week_prime_cost_change_pct, B2 = numeric change value
Row 4 header = rank, concern, financial_impact
If weekly prime cost is above 65%, include Weekly prime cost above 65% as the top concern.

For COGS Calculation: pull all the line items out of the three invoice PDFs, then work out actual COGS by category: food, beverage, and dry goods, using opening inventory plus purchases minus closing inventory. Show each category as a percentage of total weekly revenue. Flag food over 32%, beverage over 25%, and dry goods over 15%. In the flag column, write ALERT when a category is over its threshold, otherwise leave it blank.

For Theoretical vs Actual Variance: use the recipes and POS sales to estimate how much of each ingredient we should have used, then compare that against actual usage from the inventory movement and purchases. Show the variance % per ingredient and write "investigate" next to anything where the absolute variance goes above 8%.

For Labor Analysis: pull daily hours and labor cost from labor_schedule.xlsx, then show labor cost as a percentage of that same day's POS revenue. Flag any day where labor goes above 35%. In the flag column for that daily table, write ALERT when labor is above 35%, otherwise leave it blank. Add an is_weekend column: Friday and Saturday get "yes", all other days get "no". If anyone went over 40 hours this week, flag them and show the overtime premium.

For Prime Cost Summary: I want to see daily prime cost and daily prime cost % for each day. Then add the weekly prime cost %, the weekly average of daily prime cost %, and a 4 week trend using historical.csv. For the status label on each period, we use "On Track" when it's at or below 60%, "Monitor" when it's between 60 and 65%, and "Immediate Action" when it goes above 65%.

For Executive Summary: list the top 3 cost concerns ranked by financial impact, show week over week change in weekly prime cost %, and put a traffic light status in cell B1 as Green, Yellow, or Red using the same thresholds as above.

Make sure all cost and percentage values are stored as real numbers in the cells, not text.
