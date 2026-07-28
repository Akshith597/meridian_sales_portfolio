import json
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

parser = argparse.ArgumentParser(description='Build Meridian sales analysis outputs.')
parser.add_argument('--source', default='data/meridian_raw_data.xlsx', help='Path to the raw Excel workbook.')
parser.add_argument('--output', default='analysis_output', help='Directory for generated CSV/JSON analysis files.')
args = parser.parse_args()
SOURCE = Path(args.source)
OUT = Path(args.output)
OUT.mkdir(parents=True, exist_ok=True)

raw_sales = pd.read_excel(SOURCE, sheet_name='Sales_Transactions')
targets = pd.read_excel(SOURCE, sheet_name='Regional_Targets').iloc[:, :5]
sales = raw_sales[raw_sales['order_id'].notna()].copy()
sales['order_date'] = pd.to_datetime(sales['order_date'])

def summarize(group_cols):
    g = sales.groupby(group_cols, dropna=False)
    out = g.agg(
        opportunities=('order_id','size'),
        wins=('deal_status', lambda x: (x == 'Won').sum()),
        pipeline_value=('deal_value','sum'),
        revenue=('revenue','sum'),
        gross_profit=('gross_profit','sum'),
        avg_discount=('discount_pct','mean'),
        avg_deal_value=('deal_value','mean'),
    ).reset_index()
    out['win_rate'] = out['wins'] / out['opportunities']
    out['gross_margin'] = out['gross_profit'] / out['revenue'].replace(0, np.nan)
    return out

quality = {
    'raw_rows': int(len(raw_sales)),
    'valid_rows': int(len(sales)),
    'excluded_rows': int(len(raw_sales)-len(sales)),
    'columns': int(len(sales.columns)),
    'duplicate_order_ids': int(sales['order_id'].duplicated().sum()),
    'fully_duplicate_rows': int(sales.duplicated().sum()),
    'raw_missing_by_column': {k: int(v) for k, v in raw_sales.isna().sum().items()},
    'valid_missing_cells': int(sales.isna().sum().sum()),
    'date_min': str(sales['order_date'].min().date()),
    'date_max': str(sales['order_date'].max().date()),
    'invalid_status': int((~sales['deal_status'].isin(['Won','Lost'])).sum()),
    'invalid_region': int((~sales['region'].isin(['North','South','East','West','Central'])).sum()),
    'invalid_discount': int(((sales['discount_pct'] < 0) | (sales['discount_pct'] > .35)).sum()),
    'quarter_mismatch': int((sales['quarter'].astype(str).str.strip() != (sales['order_date'].dt.year.astype(str) + '-Q' + sales['order_date'].dt.quarter.astype(str))).sum()),
    'deal_value_mismatch': int((sales['deal_value'] - sales['quantity'] * sales['unit_price']).abs().gt(.011).sum()),
    'sale_price_rounding_variance_over_2_cents': int((sales['sale_unit_price'] - sales['unit_price'] * (1-sales['discount_pct'])).abs().gt(.02).sum()),
    'revenue_rule_mismatch': int((sales['revenue'] - np.where(sales['deal_status'].eq('Won'), sales['quantity'] * sales['sale_unit_price'], 0)).abs().gt(.011).sum()),
    'cost_rule_mismatch': int((sales['cost'] - np.where(sales['deal_status'].eq('Won'), sales['quantity'] * sales['unit_cost'], 0)).abs().gt(.011).sum()),
    'profit_rule_mismatch': int((sales['gross_profit'] - (sales['revenue']-sales['cost'])).abs().gt(.011).sum()),
    'target_duplicates': int(targets.duplicated(['region','quarter']).sum()),
    'target_missing': int(targets.isna().sum().sum()),
}

target_recalc = summarize(['region','quarter'])[['region','quarter','revenue']].merge(targets, on=['region','quarter'], how='outer')
quality['target_actual_revenue_mismatch'] = int((target_recalc['revenue'] - target_recalc['actual_revenue']).abs().gt(.011).sum())

region = summarize(['region']).merge(
    targets.groupby('region', as_index=False).agg(target=('quarterly_target','sum'), actual_revenue=('actual_revenue','sum')),
    on='region', how='left'
)
region['attainment'] = region['actual_revenue'] / region['target']
region['revenue_gap'] = region['actual_revenue'] - region['target']

quarter_region = summarize(['quarter','region']).merge(targets, on=['quarter','region'])
south_quarter = quarter_region[quarter_region.region.eq('South')].copy()

south = sales[sales.region.eq('South')].copy()
other = sales[~sales.region.eq('South')].copy()

rep = summarize(['region','sales_rep']).sort_values(['region','revenue'], ascending=[True,False])
south_rep = rep[rep.region.eq('South')].copy()
category = summarize(['region','product_category'])
south_category = category[category.region.eq('South')].copy()
customer = summarize(['region','customer_type'])
south_customer = customer[customer.region.eq('South')].copy()
deal_size = summarize(['region','deal_size'])
south_deal_size = deal_size[deal_size.region.eq('South')].copy()

sales['discount_band'] = pd.cut(sales.discount_pct, bins=[-0.001,.05,.10,.15,.20,.25,.30,.351], labels=['0-5%','5-10%','10-15%','15-20%','20-25%','25-30%','30%+'])
discount = summarize(['region','discount_band'])

mix = []
for dim in ['product_category','customer_type','deal_size']:
    allg = summarize([dim])
    sg = summarize([dim]) if False else summarize(['region',dim])
    sg = sg[sg.region.eq('South')]
    bench = sales[~sales.region.eq('South')].groupby(dim).agg(bench_opps=('order_id','size'),bench_wins=('deal_status',lambda x:(x=='Won').sum()),bench_revenue=('revenue','sum')).reset_index()
    bench['bench_win_rate'] = bench.bench_wins/bench.bench_opps
    m = sg.merge(bench, on=dim, how='left')
    m['win_rate_gap_pp'] = (m.win_rate-m.bench_win_rate)*100
    m['revenue_opportunity_at_benchmark_win_rate'] = ((m.bench_win_rate*m.opportunities-m.wins).clip(lower=0))*m.avg_deal_value*(1-m.avg_discount)
    m.insert(0,'dimension',dim)
    mix.append(m)
mix = pd.concat(mix, ignore_index=True)

# Rep opportunity: expected revenue if each South rep reached non-South overall win rate, holding volume/value/discount constant.
benchmark_win = (other.deal_status == 'Won').mean()
south_rep['benchmark_win_rate'] = benchmark_win
south_rep['incremental_wins_to_benchmark'] = (benchmark_win*south_rep.opportunities-south_rep.wins).clip(lower=0)
south_rep['estimated_revenue_opportunity'] = south_rep.incremental_wins_to_benchmark*south_rep.avg_deal_value*(1-south_rep.avg_discount)

stats = {
    'south_total_target': float(targets.loc[targets.region.eq('South'),'quarterly_target'].sum()),
    'south_total_revenue': float(south.revenue.sum()),
    'south_attainment': float(south.revenue.sum()/targets.loc[targets.region.eq('South'),'quarterly_target'].sum()),
    'south_gap': float(south.revenue.sum()-targets.loc[targets.region.eq('South'),'quarterly_target'].sum()),
    'south_win_rate': float((south.deal_status=='Won').mean()),
    'other_win_rate': float((other.deal_status=='Won').mean()),
    'south_avg_discount': float(south.discount_pct.mean()),
    'other_avg_discount': float(other.discount_pct.mean()),
    'south_avg_deal': float(south.deal_value.mean()),
    'other_avg_deal': float(other.deal_value.mean()),
    'south_gross_margin': float(south.gross_profit.sum()/south.revenue.sum()),
    'other_gross_margin': float(other.gross_profit.sum()/other.revenue.sum()),
    'south_pipeline': float(south.deal_value.sum()),
    'south_lost_pipeline': float(south.loc[south.deal_status.eq('Lost'),'deal_value'].sum()),
    'benchmark_win_revenue_opportunity': float((benchmark_win*len(south)-(south.deal_status=='Won').sum())*south.deal_value.mean()*(1-south.discount_pct.mean())),
}

for name, frame in {
    'clean_data': sales,
    'regional_targets': targets,
    'region_summary': region,
    'quarter_region': quarter_region,
    'south_quarter': south_quarter,
    'rep_summary': rep,
    'south_rep': south_rep,
    'category_summary': category,
    'south_category': south_category,
    'south_customer': south_customer,
    'south_deal_size': south_deal_size,
    'discount_summary': discount,
    'mix_benchmark': mix,
}.items():
    frame.to_csv(OUT / f'{name}.csv', index=False)

(OUT/'quality.json').write_text(json.dumps(quality, indent=2))
(OUT/'stats.json').write_text(json.dumps(stats, indent=2))
sales.assign(order_date=sales.order_date.dt.strftime('%Y-%m-%d')).to_json(OUT/'clean_data.json', orient='records')
targets.to_json(OUT/'regional_targets.json', orient='records')

dashboard_data = {'periods': {}, 'trend': []}
for period in ['All'] + sorted(sales.quarter.unique().tolist()):
    f = sales if period == 'All' else sales[sales.quarter.eq(period)]
    sf, bf = f[f.region.eq('South')], f[~f.region.eq('South')]
    tf = targets[targets.region.eq('South')] if period == 'All' else targets[(targets.region.eq('South')) & (targets.quarter.eq(period))]
    def dim_rows(dim):
        x = sf.groupby(dim).agg(opportunities=('order_id','size'), wins=('deal_status',lambda v:(v=='Won').sum()), revenue=('revenue','sum'), gross_profit=('gross_profit','sum'), avg_discount=('discount_pct','mean')).reset_index()
        x['win_rate'] = x.wins/x.opportunities
        x['gross_margin'] = x.gross_profit/x.revenue.replace(0,np.nan)
        return x.sort_values('revenue',ascending=False).replace({np.nan:None}).to_dict('records')
    revenue = sf.revenue.sum()
    dashboard_data['periods'][period] = {
        'kpis': {
            'revenue': float(revenue), 'target': float(tf.quarterly_target.sum()),
            'attainment': float(revenue/tf.quarterly_target.sum()),
            'win_rate': float((sf.deal_status=='Won').mean()),
            'benchmark_win_rate': float((bf.deal_status=='Won').mean()),
            'avg_discount': float(sf.discount_pct.mean()),
            'benchmark_discount': float(bf.discount_pct.mean()),
            'gross_margin': float(sf.gross_profit.sum()/revenue),
            'benchmark_margin': float(bf.gross_profit.sum()/bf.revenue.sum()),
        },
        'sales_rep': dim_rows('sales_rep'),
        'product_category': dim_rows('product_category'),
        'deal_size': dim_rows('deal_size'),
        'customer_type': dim_rows('customer_type'),
    }
for q in sorted(sales.quarter.unique().tolist()):
    sr = sales[(sales.region.eq('South')) & (sales.quarter.eq(q))]
    tr = targets[(targets.region.eq('South')) & (targets.quarter.eq(q))].iloc[0]
    dashboard_data['trend'].append({'quarter':q,'revenue':float(sr.revenue.sum()),'target':float(tr.quarterly_target),'attainment':float(sr.revenue.sum()/tr.quarterly_target)})
(OUT/'dashboard_data.json').write_text(json.dumps(dashboard_data, separators=(',',':')))
print(json.dumps({'quality': quality, 'stats': stats}, indent=2))
print('\nREGION\n', region.to_string(index=False))
print('\nSOUTH REP\n', south_rep.sort_values('estimated_revenue_opportunity', ascending=False).to_string(index=False))
print('\nSOUTH CATEGORY\n', south_category.sort_values('revenue', ascending=False).to_string(index=False))
print('\nMIX BENCHMARK TOP\n', mix.sort_values('revenue_opportunity_at_benchmark_win_rate', ascending=False).head(20).to_string(index=False))
