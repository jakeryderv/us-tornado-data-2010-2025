"""Produce a reproducible linkage coverage audit; does not claim match accuracy."""
from pathlib import Path
import argparse
import json
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.build_crosswalk import sha


def audit(data, output):
    data,output=Path(data),Path(output)
    manifest=json.loads((data/'analysis/manifest.json').read_text())
    linkage=manifest['linkage']
    for entry in manifest['inputs']:
        if sha(data/entry['path'])!=entry['sha256']:raise ValueError('Stale linkage input')
    for entry in manifest['files']:
        if sha(data/'analysis'/entry['path'])!=entry['sha256']:raise ValueError('Changed linkage output')
    crosswalk=pd.read_parquet(data/'analysis/source_crosswalk.parquet')
    linked=pd.read_parquet(data/'analysis/tornadoes.parquet')
    ncei=pd.read_parquet(data/'analysis/storm_events.parquet')
    accepted=crosswalk.loc[crosswalk.accepted]
    # Ratings are a post-linkage diagnostic only, never an input to decisions.
    pairs=accepted.loc[accepted.source_table.eq('storm_events')].merge(
        ncei[['event_id','ef_rating']],left_on='source_id',right_on='event_id',validate='one_to_one'
    ).merge(linked[['tornado_id','ef_rating']],on='tornado_id',suffixes=('_ncei','_spc'),validate='many_to_one')
    known=pairs.ef_rating_ncei.notna() & pairs.ef_rating_spc.notna()
    discordance=dict(known_pairs=int(known.sum()),different_recorded_ratings=int(
        (pairs.loc[known,'ef_rating_ncei']!=pairs.loc[known,'ef_rating_spc']).sum()))
    # Include a repeatable small sample from each stratum, plus identified boundary cases.
    parts=[]
    for _,group in crosswalk.groupby(['source_table','source_origin','match_status']):
        parts.append(group.sample(n=min(5,len(group)),random_state=42))
    examples=crosswalk.loc[crosswalk.source_id.isin(['309725','1297797','efc:2013:DAT:47712']) |
                          (crosswalk.tornado_id.eq('spc:2011:1105221634-01') & crosswalk.accepted)]
    sample=pd.concat([*parts,examples]).drop_duplicates(['source_table','source_id','tornado_id'])
    sample['spc_ef_rating_diagnostic']=sample.tornado_id.map(linked.set_index('tornado_id').ef_rating)
    sample['ncei_ef_rating_diagnostic']=sample.source_id.where(sample.source_table.eq('storm_events')).map(ncei.set_index('event_id').ef_rating)
    sample=sample.sort_values(['source_table','source_origin','match_status','source_id','candidate_rank'])
    sensitivity=[]
    for minutes,km in [(1,1),(2,2)]:
        subset=accepted.loc[accepted.interval_outside_minutes.le(minutes) & accepted.source_extent_distance_km.le(km)]
        sensitivity.append(dict(max_outside_minutes=minutes,max_extent_km=km,
            ncei_records=int(subset.source_table.eq('storm_events').sum()),
            footprint_regions=int(subset.source_table.eq('tornado_footprints').sum()),spc_tracks=int(subset.tornado_id.nunique())))
    yearly=linked.groupby('year').agg(spc_tracks=('tornado_id','size'),
        with_ncei=('ncei_accepted_records',lambda x:int(x.gt(0).sum())),
        with_footprints=('footprint_accepted_records',lambda x:int(x.gt(0).sum())),
        with_any_link=('linkage_available','sum')).reset_index()
    metrics=dict(algorithm=linkage['algorithm'],linkage_manifest_sha256=sha(data/'analysis/manifest.json'),
        summary=linkage['summary'],files=manifest['files'],threshold_sensitivity=sensitivity,
        ncei_spc_rating_diagnostic=discordance,by_year=yearly.to_dict('records'),
        source_records=int(crosswalk.groupby(['source_table','source_id']).ngroups),candidate_rows=len(crosswalk),
        review_sample_rows=len(sample),largest_suggested_split_group=int(linked.groupby('suggested_split_group').size().max()),
        accepted_reasons=accepted.decision_reason.value_counts().to_dict(),
        nonaccepted_plausible_reasons=crosswalk.loc[crosswalk.plausible & ~crosswalk.accepted].decision_reason.value_counts().to_dict())
    output.mkdir(parents=True,exist_ok=True)
    sample.to_csv(output/'review_sample.csv',index=False)
    (output/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    summary=linkage['summary']
    lines=['# SPC-centered linkage audit','',f"Algorithm `{linkage['algorithm']}`; generated {manifest['generated_at']}.",
        f"The input/output hashes and rules are recorded in `data/analysis/manifest.json` (SHA-256 `{metrics['linkage_manifest_sha256']}`).",'',
        'This is a coverage and implementation audit of automatic research links, **not an independently measured precision/recall score**. The six supporting source tables remain unchanged; the main tornado table retains every original SPC column and adds linkage summaries. No EF rating was used to select a match.','',
        '| Source records | Total | Accepted | Plausible, unresolved | Only outside acceptance | No candidates |',
        '|---|---:|---:|---:|---:|---:|']
    for r in summary['sources']:
        lines.append(f"| {r['source_origin']} / {r['source_table']} | {r['source_records']:,} | {r['accepted_records']:,} | {r['unresolved_with_plausible_candidates']:,} | {r['only_outside_acceptance']:,} | {r['no_candidates']:,} |")
    lines += ['',f"All **{len(linked):,} SPC tornadoes** and **{metrics['source_records']:,} NCEI/footprint source records** are represented. The crosswalk contains {len(crosswalk):,} candidate/no-candidate rows.",
        f"Accepted NCEI links cover **{summary['spc_with_ncei']:,}** SPC tracks; footprints cover **{summary['spc_with_footprints']:,}**; **{summary['spc_with_both']:,}** have both. **{summary['spc_without_accepted_links']:,}** have neither and remain in the linked table.",
        f"The nine main tables plus annual summary total **{sum(f['bytes'] for f in manifest['files']):,} bytes**.",'',
        '## Sensitivity','',
        'The acceptance rule is 2 minutes/2 km with a unique candidate inside the wider 10-minute/5-km ambiguity guard. Tightening the accepted set to 1 minute/1 km gives the following counts; this tests threshold dependence, not accuracy. The wider ambiguity guard stays fixed.','',
        '| Maximum interval extension / directed distance | NCEI records | Footprint regions | SPC tracks with any link |',
        '|---|---:|---:|---:|']
    for r in sensitivity:lines.append(f"| {r['max_outside_minutes']} min / {r['max_extent_km']} km | {r['ncei_records']:,} | {r['footprint_regions']:,} | {r['spc_tracks']:,} |")
    lines += ['', '## Snapshot review notes (v2.0.0 source tables)' ,'',
        '- **Moore, May 20, 2013:** `efc:2013:DAT:47712` is a unique plausible candidate for `spc:2013:1305201356-01`, with matching event timestamp and close geometry. It remains review-required because its only event time is `stormdate`; no valid polygon/line interval is present. This documents a deliberate false-negative risk from strict timestamp eligibility, not absence of the footprint.',
        '- **NCEI 309725:** a 5-minute interval discrepancy and about 4.25 km directed geometry discrepancy make this a borderline candidate for `spc:2011:1104270137-01`. Its narrative describes a substantially longer multi-county track and its recorded rating differs. It is retained for review, not accepted. The narrative/rating observations are diagnostic only.',
        '- **NCEI 1297797:** endpoint geometry agrees closely, but the interval extends 10 minutes beyond the SPC record. It remains review-required instead of accepting location alone.',
        '- **Joplin, May 22, 2011:** inspect the accepted NCEI segments and footprint regions linked to `spc:2011:1105221634-01` in the sample. Their separate rows remain separate; county totals deduplicate county/year keys.',
        '', 'These targeted inspections were made against the v2.0.0 source tables on September 15, 2026. They are historical notes if this audit is regenerated with newer inputs, not a random labeled validation set. No per-event manual override was added. `review_sample.csv` includes five deterministically sampled candidate rows per available source/status stratum plus these examples, including alternate candidates.',
        '', '## Label differences and split groups','',
        f"Among {discordance['known_pairs']:,} accepted NCEI/SPC pairs with both ratings known, {discordance['different_recorded_ratings']:,} have different recorded ratings. Segment-level maxima, later source revisions, and linkage errors can all contribute; disagreement alone cannot identify a wrong link. These labels were inspected only after matching and remain unchanged.",
        f"The suggested split grouping has {summary['split_groups']:,} groups, with up to {metrics['largest_suggested_split_group']:,} SPC tracks in one group. It combines UTC days and plausible episode/family links, can span several days, and is not a verified outbreak catalog.",
        '', '## Coverage by SPC year','', '| Year | SPC tracks | With NCEI | With footprints | With any accepted link |','|---|---:|---:|---:|---:|']
    for r in yearly.to_dict('records'):lines.append(f"| {r['year']} | {r['spc_tracks']:,} | {r['with_ncei']:,} | {r['with_footprints']:,} | {r['with_any_link']:,} |")
    lines += ['', '## Reproduce','', '```sh','uv run python scripts/build_crosswalk.py','uv run python scripts/audit_crosswalk.py','uv run python -m unittest discover -s tests -v','```','',
        'See [linkage methods](../../docs/LINKAGE.md) for thresholds, source time definitions, county context, loading examples, and limitations. This canonical linkage is included in v2.1.0 / Kaggle 6.','']
    (output/'audit.md').write_text('\n'.join(lines))
    return metrics


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/linkage')
    args=parser.parse_args()
    result=audit(args.data_dir,args.output)
    print(json.dumps(result['summary'],indent=2))
