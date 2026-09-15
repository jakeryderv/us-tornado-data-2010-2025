"""Plot saved audit metrics. Run with: uv run python scripts/plot_dat_coverage.py"""
from pathlib import Path
from datetime import datetime
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

folder = Path(__file__).resolve().parents[1] / 'reports' / 'dat_coverage'
m = json.loads((folder/'dat_coverage_metrics.json').read_text())
snapshot_date = datetime.fromisoformat(m['snapshot']).strftime('%d %B %Y')
years = list(range(2010, 2026))
fig, (ax, bx) = plt.subplots(1, 2, figsize=(12, 4.8), gridspec_kw={'width_ratios': [1.5, 1]})
lo = [m['spc_rated_yearly'][str(y)]['strict']['percent'] for y in years]
hi = [m['spc_rated_yearly'][str(y)]['loose']['percent'] for y in years]
main = [m['spc_rated_yearly'][str(y)]['standard']['percent'] for y in years]
ax.fill_between(years, lo, hi, color='#007f86', alpha=.17, label='Matching-threshold sensitivity')
ax.plot(years, main, color='#007f86', marker='o', lw=2, label='SPC EF0–EF5 tornadoes')
ax.plot(years, [m['spc_yearly'][str(y)]['standard']['percent'] for y in years],
        color='#697885', lw=1.5, ls='--', label='Including unknown ratings')
ax.set(xlim=(2009.7, 2025.3), ylim=(0, 100), ylabel='SPC records with candidate DAT tornado line (%)',
       title='Availability improves, but no complete period')
ax.set_xticks([2010,2013,2016,2019,2022,2025]);ax.legend(fontsize=8,loc='upper left',frameon=False)
classes=list(range(6)); recent=m['windows']['2023']['by_ef']; alltime=m['windows']['2010']['by_ef']
width=.36
bx.bar([x-width/2 for x in classes], [alltime[str(x)]['total'] for x in classes],width,color='#697885',label='2010–2025')
bx.bar([x+width/2 for x in classes], [recent[str(x)]['total'] for x in classes],width,color='#007f86',label='2023–2025')
bx.set_yscale('log');bx.set(xticks=classes,xticklabels=[f'EF{x}' for x in classes],ylabel='SPC labeled tornadoes (log scale)',title='A recent cutoff loses rare-class examples')
bx.legend(fontsize=8,frameon=False)
for a in (ax,bx):
    a.spines[['top','right']].set_visible(False);a.grid(axis='y',alpha=.18);a.set_axisbelow(True)
fig.suptitle('DAT coverage audit for tornado-intensity classification',fontsize=15,ha='left',x=.075)
fig.text(.075,.035,f'Candidate matches: starts within 5 km and 30 min. Band: 1 km / 10 min to 10 km / 60 min; not a confidence interval.\nSaved NOAA snapshot, {snapshot_date}. Candidate matches are not verified event identities.',fontsize=8,color='#475569')
fig.tight_layout(rect=(0,.1,1,.94))
fig.savefig(folder/'dat_coverage.png',dpi=180)
fig.savefig(folder/'dat_coverage.svg')
print('Saved coverage figure as PNG and SVG.')
