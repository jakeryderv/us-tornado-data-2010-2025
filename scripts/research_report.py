"""Build compact reproducible research coverage reports from linked release tables."""
import argparse
import json
from pathlib import Path
import pandas as pd
from enrichment.common import digest, atomic_json


def build_report(data, output):
    data,output=Path(data),Path(output);output.mkdir(parents=True,exist_ok=True)
    paths=['analysis/tornadoes.parquet','analysis/event_areas.parquet','analysis/nlcd_samples.parquet',
           'analysis/source_coverage.parquet','analysis/source_crosswalk.parquet',
           'ml/events_onset.parquet','ml/events_retrospective.parquet','ml/feature_dictionary.json']
    base=pd.read_parquet(data/paths[0]);on=pd.read_parquet(data/paths[5]);retro=pd.read_parquet(data/paths[6])
    areas=pd.read_parquet(data/paths[1]);nlcd=pd.read_parquet(data/paths[2]);coverage=pd.read_parquet(data/paths[3])
    cross=pd.read_parquet(data/paths[4]);spec=json.loads((data/paths[7]).read_text())
    frame=base[['tornado_id','state','year','ef_rating','ncei_accepted_records','footprint_accepted_records',
                'ncei_unresolved_candidate_records','footprint_unresolved_candidate_records','county_context_missing_rows']].copy()
    frame['ef_class']=frame.ef_rating.astype('string').fillna('unknown')
    metrics=pd.DataFrame({'tornado_id':frame.tornado_id,'target_known':frame.ef_rating.notna(),
        'ncei_accepted':frame.ncei_accepted_records.gt(0),'footprint_accepted':frame.footprint_accepted_records.gt(0),
        'ncei_unresolved_candidates':frame.ncei_unresolved_candidate_records.gt(0),
        'footprint_unresolved_candidates':frame.footprint_unresolved_candidate_records.gt(0),
        'county_context_missing':frame.county_context_missing_rows.gt(0)})
    # Explicitly distinguish valid zero counts from absent values and positive detections.
    for name in spec['onset_predictor_columns']:
        values=on.set_index('tornado_id')[name].reindex(frame.tornado_id)
        metrics[name+'_missing']=values.isna().to_numpy()
        if name.endswith('_count'):metrics[name+'_positive']=values.gt(0).to_numpy()
    for name in ['post_land_developed_fraction','post_land_forest_fraction','post_land_cropland_fraction','post_impervious_mean_percent','post_nlcd_valid_fraction']:
        metrics[name+'_missing']=retro.set_index('tornado_id')[name].reindex(frame.tornado_id).isna().to_numpy()
    land=nlcd.loc[nlcd['product'].eq('land_cover')].set_index('tornado_id')
    metrics['nlcd_zero_pixel_centers']=land.pixel_count.reindex(frame.tornado_id).eq(0).to_numpy()
    metrics['nlcd_no_valid_class_pixels']=land.valid_pixel_count.reindex(frame.tornado_id).eq(0).to_numpy()
    methods=areas.set_index('tornado_id').area_method.reindex(frame.tornado_id)
    for method in sorted(methods.dropna().unique()):metrics['area_method_'+method]=methods.eq(method).to_numpy()
    for source in sorted(coverage.source.unique()):
        status=coverage.loc[coverage.source.eq(source)].set_index('tornado_id').status.reindex(frame.tornado_id)
        for value in ['complete','unavailable','failed','budget_exceeded','not_requested']:
            metrics[source+'_'+value]=status.fillna('not_requested').eq(value).to_numpy()
    frame=frame[['tornado_id','year','state','ef_class']].merge(metrics,on='tornado_id',validate='one_to_one')
    names=[c for c in metrics if c!='tornado_id']
    for dimension in ['year','state','ef_class']:
        grouped=frame.groupby(dimension,dropna=False)
        counts=grouped[names].sum().astype('int64');counts.insert(0,'events',grouped.size());counts.to_csv(output/f'counts_by_{dimension}.csv')
        rates=grouped[names].mean();rates.insert(0,'events',grouped.size());rates.to_csv(output/f'fractions_by_{dimension}.csv')
    cross.groupby(['source_table','source_year','match_status'],dropna=False).size().rename('candidate_or_unmatched_rows').to_csv(output/'linkage_by_source_year.csv')
    ef=frame.ef_class.value_counts().sort_index().to_dict()
    summary=dict(schema_version=1,events=len(frame),ef_counts=ef,metric_counts={k:int(frame[k].sum()) for k in names},
                 input_sha256={p:digest(data/p) for p in paths},
                 definitions={'denominator':'All backbone tornadoes within the stratum; unknown EF is a separate class.',
                 'source_complete':'A completed extraction job, not verified radar uptime, warning-history completeness or usable pixels.',
                 'positive_count':'At least one qualifying detection/active warning in the selected onset window; not observing coverage.',
                 'missing':'Null output feature; includes no qualifying detections, incomplete state or unusable raster sampling as applicable.',
                 'linkage_rows':'Candidate and unmatched rows, not unique tornadoes or independent observations.',
                 'geography':'SPC reported state code; includes selected territories. State averages are not local radar coverage maps.',
                 'unknown_reason':'Observed patterns do not establish why a label or observation is missing.'})
    atomic_json(output/'summary.json',summary)
    mc=summary['metric_counts']
    body=f'''# Research coverage\n\nAll {len(frame):,} backbone tornadoes remain included. CSV counts/fractions use the entire\nstratum, including unknown labels, as denominator. Each file is split by year,\nSPC state/territory, or EF class. These are sample/feature summaries, not observing-system coverage estimates.\n\n- Unknown EF targets: {len(frame)-mc['target_known']:,}.\n- Land-cover fractions missing: {mc['post_land_developed_fraction_missing']:,}.\n- Impervious means missing: {mc['post_impervious_mean_percent_missing']:,}.\n- NLCD outside selected coverage: {mc['nlcd_unavailable']:,}.\n- Completed land-cover samples with zero pixel centers: {mc['nlcd_zero_pixel_centers']:,}.\n- Onset shear missing: {mc['radar_max_shear_per_s_missing']:,}.\n- Onset tornado-warning lead missing: {mc['warning_tornado_lead_minutes_missing']:,}.\n\nEF counts: {json.dumps(ef,sort_keys=True)}.\n\n`summary.json` records input hashes and metric definitions. `counts_by_*` and\n`fractions_by_*` cover known targets, accepted/unresolved links, county context,\nradar/warning feature missingness and positive detections, source job status,\nfootprint method, and usable NLCD sampling. `linkage_by_source_year.csv` retains\nall decision categories; candidate-row counts must not be interpreted as event counts.\n\nA zero radar count is no qualifying product record, not proof of no rotation or\nworking radar coverage. A zero active-warning count is the reconstructed archive\nstate, not an independently complete warning history. Missing lead time can mean\nno active tornado warning or missing original issuance. No coverage adjustment,\nlabel correction, imputation, causal inference or train/test fitting is performed.\n\nRegenerate with `python -m scripts.research_report --data-dir DATA --output OUTPUT`.\n'''
    (output/'README.md').write_text(body)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',type=Path,default=Path('data'));p.add_argument('--output',type=Path,default=Path('reports/research'))
    a=p.parse_args();r=build_report(a.data_dir,a.output);print(json.dumps({'events':r['events'],'output':str(a.output)}))

if __name__=='__main__':main()
