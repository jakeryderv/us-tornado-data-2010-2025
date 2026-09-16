"""Independent, vectorized reconstruction of onset aggregates from linked evidence."""
import json
import numpy as np
import pandas as pd
from .common import utc
from .features import RADAR_FEATURES


def verify_onset(onset, tables, config):
    """Check all aggregate values and timestamp eligibility; does not assert receipt."""
    ids=onset.tornado_id
    cutoff=onset[['tornado_id','prediction_cutoff_utc']]
    actual=onset.set_index('tornado_id')
    checked=[]
    def compare(name, values, source):
        expected=values.reindex(ids)
        expected.loc[~actual.loc[ids,source+'_source_status'].eq('complete').to_numpy()]=np.nan
        a=pd.to_numeric(actual.loc[ids,name],errors='raise').to_numpy(dtype=float,na_value=np.nan)
        b=expected.to_numpy(dtype=float,na_value=np.nan)
        if not np.allclose(a,b,equal_nan=True,rtol=1e-12,atol=1e-12):
            raise ValueError(f'Onset source-time reconstruction differs: {name}')
        checked.append(name)
    radar=tables['tornado_radar']
    if len(radar):
        detections=tables['radar_detections'].drop(columns=['geometry','raw_json'],errors='ignore')
        radar=radar.merge(detections,on='record_id',validate='many_to_one').merge(cutoff,on='tornado_id',validate='many_to_one')
        observed,available=utc(radar.observed_at),utc(radar.available_at)
        if observed.isna().any() or available.isna().any():raise ValueError('Invalid radar timestamp')
        if not (available-observed).eq(pd.Timedelta(minutes=config['radar_latency_minutes'])).all():
            raise ValueError('Radar availability differs from configured assumed latency')
        lower=radar.prediction_cutoff_utc-pd.Timedelta(minutes=config['radar_before_minutes'])
        upper=radar.prediction_cutoff_utc+pd.Timedelta(minutes=config['radar_after_minutes'])
        if not (observed.between(lower,upper) & radar.distance_km.between(0,config['radar_radius_km'])).all():
            raise ValueError('Radar association outside configured window/radius')
        eligible=radar.loc[available<=radar.prediction_cutoff_utc]
        if (utc(eligible.observed_at)>eligible.prediction_cutoff_utc).any():raise ValueError('Future radar observation at onset')
    else:eligible=radar
    for name,(product,column) in RADAR_FEATURES.items():
        rows=eligible.loc[eligible['product'].eq(product)] if len(eligible) else eligible
        if column is None:
            values=rows.groupby('tornado_id').size().reindex(ids,fill_value=0) if len(rows) else pd.Series(0,index=ids)
        else:
            values=(rows.assign(_value=pd.to_numeric(rows[column],errors='coerce')).groupby('tornado_id')._value.max()
                    if len(rows) else pd.Series(dtype=float))
        compare(name,values,'radar')
    radar_rows=len(eligible)
    del radar,eligible
    warnings=tables['tornado_warnings']
    invalid=set()
    active=pd.DataFrame()
    if len(warnings):
        updates=tables['warning_updates'].drop(columns=['geometry'],errors='ignore')
        warnings=warnings.merge(updates,on=['record_id','warning_id'],validate='many_to_one').merge(cutoff,on='tornado_id',validate='many_to_one')
        warnings['_issued']=utc(warnings.issued_at)
        if warnings._issued.isna().any():raise ValueError('Invalid warning issuance timestamp')
        before=warnings.loc[warnings._issued<=warnings.prediction_cutoff_utc]
        latest=before.loc[before._issued==before.groupby(['tornado_id','warning_id'])._issued.transform('max')]
        invalid=set(latest.loc[latest.action.isna() | latest.known_expiry_at.isna(),'tornado_id'])
        active=latest.loc[latest.covers_start & ~latest.action.isin(['CAN','EXP']) &
                          (utc(latest.known_expiry_at)>latest.prediction_cutoff_utc)].drop_duplicates(['tornado_id','warning_id'])
        if len(active):
            if (utc(active.original_issue_at)>active.prediction_cutoff_utc).any():raise ValueError('Future original warning issuance')
            if 'raw_json' in active:
                poly=active.raw_json.map(lambda s:json.loads(s).get('POLY_BEG'))
                poly=pd.to_datetime(poly,format='%Y%m%d%H%M',utc=True,errors='coerce')
                if poly.isna().any() or (poly>active.prediction_cutoff_utc).any():raise ValueError('Invalid/future warning polygon time')
    for phenomenon,name in [('TO','warning_active_tornado_count'),('SV','warning_active_severe_count')]:
        rows=active.loc[active.phenomenon.eq(phenomenon)] if len(active) else active
        values=rows.groupby('tornado_id').size().reindex(ids,fill_value=0).astype(float) if len(rows) else pd.Series(0.,index=ids)
        values.loc[values.index.isin(invalid)]=np.nan
        compare(name,values,'warnings')
    lead=pd.Series(np.nan,index=ids)
    if len(active):
        tor=active.loc[active.phenomenon.eq('TO')].copy();tor['_original']=utc(tor.original_issue_at)
        first=tor.groupby('tornado_id')._original.min()
        missing=set(tor.loc[tor._original.isna(),'tornado_id'])|invalid
        values=(actual.prediction_cutoff_utc-first).dt.total_seconds()/60
        lead=values.reindex(ids);lead.loc[lead.index.isin(missing)]=np.nan
    compare('warning_tornado_lead_minutes',lead,'warnings')
    return dict(status='passed',aggregate_columns=checked,eligible_radar_links=radar_rows,
                active_warning_event_rows=len(active),future_selected_observations=0,
                actual_receipt_verified=False,
                scope='independent source-time/value reconstruction; availability and cancellation completeness remain qualified')
