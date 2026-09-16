"""ERA5 request contracts, real GRIB decoding, and retrospective-only features."""
from dataclasses import asdict
import io
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from enrichment.common import Cache,Config
from enrichment.era5 import (SURFACE_DATASET,SURFACE_FIELDS,PRESSURE_DATASET,PRESSURE_FIELDS,
                             PRESSURE_LEVELS,decode_batch,environmental_summary,plan_requests)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('eccodes','metpy')), 'Install the optional era5 dependency group')
class Era5Tests(unittest.TestCase):
    def events(self):
        return [dict(tornado_id=v,start_utc=t,latitude=35.1,longitude=-97.1,usable=True)
                for v,t in [('a','2010-01-01T12:15Z'),('b','2010-01-02T13:15Z')]]

    def test_batching_requests_both_level_sets_and_exact_calendar_subsets(self):
        plans=plan_requests(self.events(),Config())
        self.assertIs(plans['a'],plans['b'])
        surface=plans['a']['requests'][SURFACE_DATASET]
        self.assertEqual(surface['area'],[40,-100,35,-95])
        self.assertEqual(surface['day'],['01','02']);self.assertEqual(surface['time'],['12:00','13:00'])
        self.assertEqual(surface['product_type'],['reanalysis'])
        self.assertIn('convective_available_potential_energy',surface['variable'])
        self.assertEqual(plans['a']['requests'][PRESSURE_DATASET]['pressure_level'],list(map(str,PRESSURE_LEVELS)))

    def write_grib(self,path,*,duplicate=False):
        from eccodes import codes_grib_new_from_samples,codes_set,codes_set_values,codes_write,codes_release
        with path.open('wb') as stream:
            for param in [*SURFACE_FIELDS,*([59] if duplicate else [])]:
                h=codes_grib_new_from_samples('regular_ll_sfc_grib1')
                for key,val in dict(Ni=3,Nj=3,latitudeOfFirstGridPointInDegrees=35.25,
                    longitudeOfFirstGridPointInDegrees=262.75,latitudeOfLastGridPointInDegrees=34.75,
                    longitudeOfLastGridPointInDegrees=263.25,iDirectionIncrementInDegrees=.25,
                    jDirectionIncrementInDegrees=.25,dataDate=20100101,dataTime=1200,step=0,
                    paramId=param,experimentVersionNumber='0001').items():codes_set(h,key,val)
                codes_set_values(h,np.full(9,100.));codes_write(h,stream);codes_release(h)

    def test_real_grib_reader_preserves_2010_samples_and_rejects_duplicates(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'source.grib';self.write_grib(p)
            rows=decode_batch(p,'asset:1',SURFACE_DATASET,self.events()[:1],Config())['a']
            self.assertEqual(len(rows),7)
            self.assertTrue(all(r['value']==100 and r['status']=='sampled' for r in rows))
            self.assertTrue(all(r['retrospective'] and not r['available_by_onset'] for r in rows))
            self.write_grib(p,duplicate=True)
            with self.assertRaisesRegex(ValueError,'Duplicate ERA5'):decode_batch(p,'x',SURFACE_DATASET,self.events()[:1],Config())

    def test_signed_download_url_is_lazy_and_never_persisted(self):
        class Response(io.BytesIO):
            status=200
            url='https://download.test/file?signature=private'
            headers={'Content-Length':'8'}
        with TemporaryDirectory() as d:
            cache=Cache(d)
            resolver=lambda:'https://download.test/file?signature=private'
            with patch('enrichment.common.urlopen',return_value=Response(b'GRIBtest')):
                _,aid=cache.fetch(resolver,suffix='.grib',identity_url='https://cds.test/selection/one',
                                  request_metadata={'year':2010})
            self.assertNotIn('private',str(cache.assets[aid]))
            def forbidden():raise AssertionError('A cache hit must not submit another CDS job')
            cache.fetch(forbidden,suffix='.grib',identity_url='https://cds.test/selection/one')

    def profile(self):
        rows=[]
        def add(variable,value,level=None):
            rows.append(dict(variable=variable,value=value,level_hpa=level,
                level_type='single' if level is None else 'pressure',valid_at='2020-01-01T00:00Z',
                status='sampled',distance_km=2))
        for name,value in [('cape_j_kg',1000),('surface_pressure_pa',90000),
            ('surface_geopotential_m2_s2',0),('wind_u_10m_m_s',0),('wind_v_10m_m_s',0)]:add(name,value)
        for level,z in [(1000,500),(850,1000),(700,3000),(500,6000),(300,9000)]:
            add('wind_u_m_s',999 if level==1000 else z/600,level)
            add('wind_v_m_s',0,level);add('geopotential_m2_s2',z*9.80665,level)
        return pd.DataFrame(rows)

    def test_shear_and_srh_exclude_below_ground_pressure_levels(self):
        result=environmental_summary(self.profile(),pd.Timestamp('2020-01-01T00:30Z'))
        self.assertAlmostEqual(result['bulk_shear_0_1km_m_s'],10/6)
        self.assertAlmostEqual(result['bulk_shear_0_6km_m_s'],10)
        self.assertAlmostEqual(result['srh_0_1km_m2_s2'],12.5)
        self.assertAlmostEqual(result['srh_0_3km_m2_s2'],37.5)
        bad=self.profile();bad['valid_at']='2020-01-01T01:00Z'
        self.assertIsNone(environmental_summary(bad,pd.Timestamp('2020-01-01T00:30Z'))['cape_j_kg'])

    def test_insufficient_profile_remains_missing(self):
        rows=self.profile();rows=rows.loc[rows.level_hpa.isna()|rows.level_hpa.gt(500)]
        result=environmental_summary(rows,pd.Timestamp('2020-01-01T00:30Z'))
        self.assertEqual(result['profile_status'],'insufficient_height_for_storm_motion')
        self.assertIsNone(result['srh_0_1km_m2_s2']);self.assertIsNone(result['bulk_shear_0_6km_m_s'])


if __name__=='__main__':unittest.main()
