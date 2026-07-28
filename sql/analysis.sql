/*
Meridian Building Supply — Southern Region Sales Diagnostic
Assumptions:
  1. Excel sheets are loaded as sales_transactions and regional_targets.
  2. One malformed aggregate-like row has NULL order_id; clean_sales excludes it.
  3. Syntax is ANSI-style and may need small date-function changes by warehouse.
*/

-- 1) Reusable cleaned view
CREATE OR REPLACE VIEW clean_sales AS
SELECT
    order_id,
    CAST(order_date AS DATE) AS order_date,
    TRIM(quarter) AS quarter,
    TRIM(region) AS region,
    TRIM(sales_rep) AS sales_rep,
    TRIM(product_category) AS product_category,
    TRIM(product_name) AS product_name,
    TRIM(customer_type) AS customer_type,
    TRIM(deal_size) AS deal_size,
    CAST(quantity AS INTEGER) AS quantity,
    CAST(unit_price AS DECIMAL(18,2)) AS unit_price,
    CAST(discount_pct AS DECIMAL(9,6)) AS discount_pct,
    CAST(deal_value AS DECIMAL(18,2)) AS deal_value,
    TRIM(deal_status) AS deal_status,
    CAST(sale_unit_price AS DECIMAL(18,2)) AS sale_unit_price,
    CAST(revenue AS DECIMAL(18,2)) AS revenue,
    CAST(unit_cost AS DECIMAL(18,2)) AS unit_cost,
    CAST(cost AS DECIMAL(18,2)) AS cost,
    CAST(gross_profit AS DECIMAL(18,2)) AS gross_profit
FROM sales_transactions
WHERE order_id IS NOT NULL;

-- 2) Data-quality checks; all exception counts should be zero after cleaning
SELECT
    COUNT(*) AS valid_rows,
    COUNT(*) - COUNT(DISTINCT order_id) AS duplicate_order_ids,
    SUM(CASE WHEN region NOT IN ('North','South','East','West','Central') THEN 1 ELSE 0 END) AS invalid_regions,
    SUM(CASE WHEN deal_status NOT IN ('Won','Lost') THEN 1 ELSE 0 END) AS invalid_statuses,
    SUM(CASE WHEN discount_pct < 0 OR discount_pct > 0.35 THEN 1 ELSE 0 END) AS invalid_discounts,
    SUM(CASE WHEN ABS(deal_value - quantity * unit_price) > 0.01 THEN 1 ELSE 0 END) AS deal_value_mismatches,
    SUM(CASE WHEN ABS(gross_profit - (revenue - cost)) > 0.01 THEN 1 ELSE 0 END) AS gross_profit_mismatches
FROM clean_sales;

-- 3) Regional scorecard: target attainment, win rate, pricing, and margin
WITH performance AS (
    SELECT
        region,
        COUNT(*) AS opportunities,
        SUM(CASE WHEN deal_status = 'Won' THEN 1 ELSE 0 END) AS wins,
        SUM(deal_value) AS pipeline_value,
        SUM(revenue) AS revenue,
        SUM(gross_profit) AS gross_profit,
        AVG(discount_pct) AS avg_discount,
        AVG(deal_value) AS avg_deal_value
    FROM clean_sales
    GROUP BY region
), targets AS (
    SELECT region, SUM(quarterly_target) AS target
    FROM regional_targets
    GROUP BY region
)
SELECT
    p.region,
    p.opportunities,
    p.wins,
    1.0 * p.wins / NULLIF(p.opportunities,0) AS win_rate,
    p.avg_discount,
    p.avg_deal_value,
    p.revenue,
    t.target,
    p.revenue / NULLIF(t.target,0) AS attainment,
    p.revenue - t.target AS revenue_gap,
    p.gross_profit / NULLIF(p.revenue,0) AS gross_margin
FROM performance p
JOIN targets t ON p.region = t.region
ORDER BY attainment;

-- 4) South quarterly trend: shows whether the miss is isolated or persistent
SELECT
    t.quarter,
    t.quarterly_target AS target,
    SUM(s.revenue) AS revenue,
    SUM(s.revenue) / NULLIF(t.quarterly_target,0) AS attainment,
    1.0 * SUM(CASE WHEN s.deal_status='Won' THEN 1 ELSE 0 END) / COUNT(*) AS win_rate,
    AVG(s.discount_pct) AS avg_discount,
    SUM(s.gross_profit) / NULLIF(SUM(s.revenue),0) AS gross_margin
FROM clean_sales s
JOIN regional_targets t
  ON s.region=t.region AND s.quarter=t.quarter
WHERE s.region='South'
GROUP BY t.quarter, t.quarterly_target
ORDER BY t.quarter;

-- 5) Rep diagnostic with company benchmark
WITH benchmark AS (
    SELECT 1.0 * SUM(CASE WHEN deal_status='Won' THEN 1 ELSE 0 END) / COUNT(*) AS benchmark_win_rate
    FROM clean_sales
    WHERE region <> 'South'
)
SELECT
    s.sales_rep,
    COUNT(*) AS opportunities,
    SUM(CASE WHEN s.deal_status='Won' THEN 1 ELSE 0 END) AS wins,
    1.0 * SUM(CASE WHEN s.deal_status='Won' THEN 1 ELSE 0 END) / COUNT(*) AS win_rate,
    b.benchmark_win_rate,
    b.benchmark_win_rate - 1.0 * SUM(CASE WHEN s.deal_status='Won' THEN 1 ELSE 0 END) / COUNT(*) AS win_rate_gap,
    AVG(s.discount_pct) AS avg_discount,
    SUM(s.revenue) AS revenue,
    SUM(s.gross_profit) / NULLIF(SUM(s.revenue),0) AS gross_margin
FROM clean_sales s
CROSS JOIN benchmark b
WHERE s.region='South'
GROUP BY s.sales_rep, b.benchmark_win_rate
ORDER BY win_rate;

-- 6) Product-category diagnostic with matched non-South benchmark
WITH category_perf AS (
    SELECT
        CASE WHEN region='South' THEN 'South' ELSE 'Benchmark' END AS cohort,
        product_category,
        COUNT(*) AS opportunities,
        SUM(CASE WHEN deal_status='Won' THEN 1 ELSE 0 END) AS wins,
        SUM(revenue) AS revenue,
        SUM(gross_profit) AS gross_profit,
        AVG(discount_pct) AS avg_discount
    FROM clean_sales
    GROUP BY CASE WHEN region='South' THEN 'South' ELSE 'Benchmark' END, product_category
)
SELECT
    s.product_category,
    s.opportunities,
    1.0*s.wins/NULLIF(s.opportunities,0) AS south_win_rate,
    1.0*b.wins/NULLIF(b.opportunities,0) AS benchmark_win_rate,
    1.0*s.wins/NULLIF(s.opportunities,0) - 1.0*b.wins/NULLIF(b.opportunities,0) AS win_rate_gap,
    s.avg_discount,
    s.revenue,
    s.gross_profit/NULLIF(s.revenue,0) AS gross_margin
FROM category_perf s
JOIN category_perf b ON s.product_category=b.product_category
WHERE s.cohort='South' AND b.cohort='Benchmark'
ORDER BY win_rate_gap;

-- 7) Large-deal loss queue for action planning
SELECT
    sales_rep,
    product_category,
    customer_type,
    COUNT(*) AS lost_opportunities,
    SUM(deal_value) AS lost_pipeline,
    AVG(discount_pct) AS avg_discount
FROM clean_sales
WHERE region='South' AND deal_status='Lost' AND deal_size IN ('Large','Enterprise')
GROUP BY sales_rep, product_category, customer_type
HAVING COUNT(*) >= 2
ORDER BY lost_pipeline DESC;
