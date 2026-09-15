"""CLI and inspection regression tests, using local synthetic fixtures only."""

from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
import csv
import gzip
import io
import json
import unittest

import download_data
from dataset_inspection import (csv_preview, dat_preview, inventory,
                                load_json, local_path, reference_check)


class Response(io.BytesIO):
    def __init__(self, url, body):
        super().__init__(body)
        self.url = url
        self.headers = {'Content-Length': str(len(body))}


class Workflow(unittest.TestCase):
    def test_dry_run_has_no_io_side_effects(self):
        with TemporaryDirectory() as d:
            target = Path(d)/'not-created'
            with patch('download_data.urlopen', side_effect=AssertionError('network')), redirect_stdout(io.StringIO()):
                self.assertEqual(download_data.main(['--dry-run','--data-dir',str(target)]),0)
            self.assertFalse(target.exists())

    def test_help_and_invalid_arguments_do_not_download(self):
        for args in [['--help'], ['--dat-batch-size','0'], ['--timeout','0'],
                     ['--start-year','2025','--end-year','2020']]:
            with self.subTest(args=args), patch('download_data.urlopen', side_effect=AssertionError('network')),\
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    download_data.main(args)
                self.assertEqual(exc.exception.code,0 if '--help' in args else 2)

    def test_original_collection_preserved_without_notebook(self):
        spc = ('yr,date,om,time,tz,st,stf,slat,slon,mag\n'
               '2019,2019-05-01,1,12:00:00,3,OK,40,35,-97,0\n'
               '2020,2020-05-01,2,12:00:00,3,OK,40,35,-97,1\n').encode()
        tables = {
            'details':'EVENT_ID,EVENT_TYPE,BEGIN_YEARMONTH\n1,Tornado,202005\n2,Hail,202005\n',
            'fatalities':'EVENT_ID,FAT_YEARMONTH\n1,202005\n2,202005\n',
            'locations':'EVENT_ID,YEARMONTH\n1,202005\n2,202005\n',
        }
        def response(request, **kwargs):
            url = request.full_url
            self.assertIn(urlparse(url).hostname, {'www.spc.noaa.gov', 'www.ncei.noaa.gov', 'services.dat.noaa.gov'})
            if url == 'https://www.spc.noaa.gov/wcm/':
                body = b'<a href="1950-2020_actual_tornadoes.csv">csv</a>'
            elif url.endswith('actual_tornadoes.csv'):
                body = spc
            elif url == 'https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/':
                body = ''.join(f'<a href="StormEvents_{t}-ftp_v1.0_d2020_c20210101.csv.gz">file</a>' for t in tables).encode()
            elif url.endswith('.csv.gz'):
                table = next(t for t in tables if f'StormEvents_{t}-' in url)
                body = gzip.compress(tables[table].encode())
            else:
                parsed = urlparse(url);query = parse_qs(parsed.query)
                if parsed.path.endswith('MapServer'):
                    value = {'layers':[{'id':i} for i in (0,1,2)]}
                elif not parsed.path.endswith('/query'):
                    value = {'fields':[{'name':'objectid','type':'esriFieldTypeOID'},
                                       {'name':'stormdate','type':'esriFieldTypeDate'}], 'maxRecordCount':200}
                elif 'returnIdsOnly' in query:
                    value = {'objectIdFieldName':'objectid','objectIds':[1]}
                elif 'returnCountOnly' in query:
                    value = {'count':1}
                else:
                    value = {'type':'FeatureCollection','features':[{
                        'type':'Feature','properties':{'objectid':1,'stormdate':1588352400000,
                                                      'efscale':'EF1','globalid':'g','event_id':'e'},
                        'geometry':{'type':'Point','coordinates':[-97,35]}}]}
                body = json.dumps(value).encode()
            return Response(url,body)
        with TemporaryDirectory() as d, patch('download_data.urlopen',side_effect=response), redirect_stdout(io.StringIO()):
            self.assertEqual(download_data.main(['--sources','noaa','--verify-downloads','--start-year','2020','--end-year','2020','--data-dir',d]),0)
            data = Path(d)
            manifest = load_json(data/'download_manifest_2020_2020.json')
            self.assertTrue(manifest['requested_archives_available'])
            self.assertEqual(len(manifest['outputs']),7)
            self.assertEqual(csv_preview(data/'spc/tornadoes_2020_2020.csv')[0]['yr'],'2020')
            self.assertEqual(len(csv_preview(data/'spc/tornadoes_2020_2020.csv')),1)
            for table in tables:
                rows = csv_preview(data/f'ncei_storm_events/tornado/2020_{table}.csv')
                self.assertEqual([r['EVENT_ID'] for r in rows],['1'])
            self.assertEqual(dat_preview(data,manifest)[0]['efscale'],'EF1')
            self.assertEqual(load_json(data/'quality_summary_2020_2020.json')['spc_rating_totals'],{'1':1})
            self.assertEqual(load_json(data/'download_verification_2020_2020.json')['status'],'passed')
            self.assertEqual({p.name for p in data.iterdir() if p.is_dir()}, {'spc','ncei_storm_events','nws_dat'})

    def test_inspection_handles_empty_partial_and_stale_files(self):
        with TemporaryDirectory() as d:
            data = Path(d)
            self.assertEqual(inventory(data),[])
            self.assertEqual(load_json(data/'missing.json',{}),{})
            self.assertEqual(csv_preview(data/'missing.csv'),[])
            with self.assertRaises(ValueError):local_path(data,'../outside')
            p = data/'rows.csv';p.write_text('code,value\n001,3\n002,4\n')
            self.assertEqual(csv_preview(p,1),[{'code':'001','value':'3'}])
            self.assertEqual(reference_check(data,{'path':'rows.csv','sha256':'bad'})['status'],'changed')

    def test_inspection_notebook_empty_and_populated_fixtures(self):
        import os
        import hashlib
        os.environ.setdefault('MPLBACKEND','Agg')
        os.environ.setdefault('MPLCONFIGDIR','/tmp/tornado-inspection-mpl')
        import matplotlib.pyplot as plt
        import nbformat
        notebook=nbformat.read(download_data.ROOT/'notebooks'/'tornado_dataset.ipynb',as_version=4)
        nbformat.validate(notebook)
        with TemporaryDirectory() as d:
            data=Path(d)
            for populated in (False,True):
                if populated:
                    spc=data/'spc/tracks.csv';spc.parent.mkdir()
                    spc.write_text('yr,om,date,time,tz,st,mag,slat,slon\n2020,1,2020-05-01,12:00:00,3,OK,1,35,-97\n')
                    manifest={'requested_archives_available':True,'outputs':[{'source':'SPC','path':'spc/tracks.csv'}],
                              'year_coverage':[{'source':'SPC','product':'tracks','available_years':[2020],
                                                'rows_by_year':{'2020':1}}]}
                    (data/'download_manifest_2010_2025.json').write_text(json.dumps(manifest))
                before={str(p):p.read_bytes() for p in data.rglob('*') if p.is_file()}
                namespace={}
                with patch('socket.create_connection',side_effect=AssertionError('No network in inspection')),redirect_stdout(io.StringIO()):
                    for cell in notebook.cells:
                        if cell.cell_type=='code':
                            exec(compile(cell.source,'inspection-fixture-cell','exec'),namespace)
                            # Override only the user-facing local DATA setting after setup.
                            namespace['DATA']=data
                plt.close('all')
                after={str(p):p.read_bytes() for p in data.rglob('*') if p.is_file()}
                self.assertEqual(before,after)


if __name__ == '__main__':
    unittest.main()
