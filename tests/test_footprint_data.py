"""Footprint source contracts: provenance, annual boundaries, and missingness."""
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import copy
import hashlib
import unittest

from footprint_data import validate_collection, verify_object


def collection():
    return {'type':'FeatureCollection','features':[{'type':'Feature','properties':{
        'source':'DAT','objectid':7,'stormdate':'2010-05-01T12:00:00Z',
        'efscale':'EFU','width':0.99,'parents':[],'children':[8]},
        'geometry':{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]}}]}


class FootprintContracts(unittest.TestCase):
    def test_annual_boundary_unknowns_and_coverage_counts(self):
        value=collection();before=copy.deepcopy(value)
        result=validate_collection(value,2010)
        self.assertEqual(value,before)
        self.assertEqual(result['source_counts'],{'DAT':1,'SED':0})
        self.assertEqual(result['quality']['width_placeholder'],1)
        value['features'][0]['properties']['stormdate']='2011-01-01T05:00:00Z'
        self.assertEqual(validate_collection(value,2010)['quality']['utc_year_differs'],1)
        for date in ['2011-01-03T00:00:00Z','2010-05-01T12:00:00','2010-05-01T12:00:00-05:00']:
            value['features'][0]['properties']['stormdate']=date
            with self.assertRaises(ValueError):validate_collection(value,2010)

    def test_unexpected_sources_duplicate_ids_and_relationships_rejected(self):
        value=collection();value['features']*=2
        with self.assertRaisesRegex(ValueError,'unique'):validate_collection(value,2010)
        for field,bad in [('source','RADAR'),('objectid',True),('parents','7')]:
            value=collection();value['features'][0]['properties'][field]=bad
            with self.assertRaises(ValueError):validate_collection(value,2010)

    def test_upstream_md5_and_size_are_independent_of_local_sidecar(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'sample.geojson';body=b'original source';p.write_bytes(body)
            item={'size':str(len(body)),'md5Hash':base64.b64encode(hashlib.md5(body).digest()).decode()}
            verify_object(p,item)
            p.write_bytes(b'changed! source')
            with self.assertRaisesRegex(ValueError,'MD5'):verify_object(p,item)
            p.write_bytes(body)
            with self.assertRaisesRegex(ValueError,'size'):verify_object(p,{**item,'size':'1'})


if __name__=='__main__':unittest.main()
