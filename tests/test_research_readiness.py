"""Regression tests for the research availability contract and independent checks."""
import unittest
from dataclasses import asdict
import pandas as pd
from enrichment.common import Config
from enrichment.features import dictionary, RADAR_FEATURES, radar_summary, warning_summary
from enrichment.temporal import verify_onset


class ReadinessTests(unittest.TestCase):
    def sample(self):
        cutoff=pd.Timestamp('2020-01-01T00:30Z')
        rows=pd.DataFrame([dict(record_id='a',product='nx3tvs',observed_at='2020-01-01T00:20Z',max_shear_per_s=.02,velocity_difference_knots=50),
                           dict(record_id='b',product='nx3tvs',observed_at='2020-01-01T00:29Z',max_shear_per_s=999,velocity_difference_knots=999)])
        links=pd.DataFrame([dict(tornado_id='one',record_id='a',distance_km=1,available_at='2020-01-01T00:25Z'),
                            dict(tornado_id='one',record_id='b',distance_km=1,available_at='2020-01-01T00:34Z')])
        updates=pd.DataFrame([dict(record_id='w1',warning_id='w',issued_at='2020-01-01T00:00Z',original_issue_at='2020-01-01T00:00Z',action='NEW',known_expiry_at='2020-01-01T01:00Z',phenomenon='TO'),
                              dict(record_id='w2',warning_id='w',issued_at='2020-01-01T00:40Z',original_issue_at='2020-01-01T00:00Z',action='CAN',known_expiry_at='2020-01-01T01:00Z',phenomenon='TO')])
        wl=pd.DataFrame([dict(tornado_id='one',record_id=r,warning_id='w',covers_start=True) for r in ['w1','w2']])
        onset=pd.DataFrame([dict(tornado_id='one',prediction_cutoff_utc=cutoff,radar_source_status='complete',warnings_source_status='complete',
            **radar_summary(links.merge(rows,on='record_id'),cutoff,complete=True),
            **warning_summary(wl.merge(updates,on=['record_id','warning_id']),cutoff,complete=True))])
        return onset,dict(radar_detections=rows,tornado_radar=links,warning_updates=updates,tornado_warnings=wl)

    def test_future_radar_and_warning_changes_do_not_affect_onset(self):
        onset,tables=self.sample()
        result=verify_onset(onset,tables,asdict(Config()))
        self.assertEqual(result['eligible_radar_links'],1)
        self.assertEqual(result['active_warning_event_rows'],1)
        tables['radar_detections'].loc[1,'max_shear_per_s']=99999
        tables['warning_updates'].loc[1,'action']='CON'
        verify_onset(onset,tables,asdict(Config()))

    def test_future_value_leak_and_changed_latency_are_rejected(self):
        onset,tables=self.sample();onset.loc[0,'radar_max_shear_per_s']=999
        with self.assertRaisesRegex(ValueError,'reconstruction differs'):verify_onset(onset,tables,asdict(Config()))
        onset,tables=self.sample();tables['tornado_radar'].loc[1,'available_at']='2020-01-01T00:29Z'
        with self.assertRaisesRegex(ValueError,'assumed latency'):verify_onset(onset,tables,asdict(Config()))

    def test_future_polygon_is_rejected(self):
        onset,tables=self.sample()
        tables['warning_updates']['raw_json']='{"POLY_BEG":"202001010040"}'
        with self.assertRaisesRegex(ValueError,'future warning polygon'):verify_onset(onset,tables,asdict(Config()))

    def test_missing_source_is_not_zero_filled(self):
        onset,tables=self.sample();onset['radar_source_status']='unavailable'
        for name in RADAR_FEATURES:onset[name]=float('nan')
        verify_onset(onset,tables,asdict(Config()))
        onset['radar_tvs_count']=0
        with self.assertRaisesRegex(ValueError,'reconstruction differs'):verify_onset(onset,tables,asdict(Config()))

    def test_contract_is_explicit_and_quality_is_not_a_default_predictor(self):
        columns=['target_ef_rating','start_latitude','radar_tvs_count','new_unreviewed_numeric']
        onset=pd.DataFrame(columns=columns);retro=pd.DataFrame(columns=columns+['post_nlcd_valid_fraction','post_path_width_yards'])
        spec=dictionary(onset,retro,asdict(Config()))
        self.assertNotIn('new_unreviewed_numeric',spec['onset_predictor_columns'])
        self.assertNotIn('post_nlcd_valid_fraction',spec['retrospective_predictor_columns'])
        self.assertIsNone(spec['columns']['start_latitude']['available_by_onset'])
        self.assertTrue(spec['columns']['start_latitude']['eligible_for_conditional_onset'])
        self.assertEqual(spec['columns']['radar_tvs_count']['availability_basis'],'observation_plus_assumed_latency')
        self.assertEqual(spec['columns']['post_path_width_yards']['units'],'yards')

if __name__=='__main__':unittest.main()
