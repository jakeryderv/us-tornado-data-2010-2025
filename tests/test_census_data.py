"""Offline source fixtures exercise year selection, geography gaps, and verification."""
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv
import io
import json
import unittest
import zipfile

import census_data as census
import download_data
from scripts.verify_downloads import verify


def csv_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode('cp1252')


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


def fixtures():
    def record(state, county, stname, name, prefix, years):
        return dict(SUMLEV='050', STATE=state, COUNTY=county, STNAME=stname, CTYNAME=name,
                    **{f'{prefix}{y}':str(y * (10 if prefix == 'POPESTIMATE' else 2)) for y in years})
    old = [record('01','001','Alabama','Autauga County','POPESTIMATE',range(2010,2021))]
    new = [record('01','001','Alabama','Autauga County','POPESTIMATE',range(2020,2026)),
           record('09','110','Connecticut','Capitol Planning Region','POPESTIMATE',range(2020,2026))]
    housing = [record('01','001','Alabama','Autauga County','HUESTIMATE',range(2010,2021))]
    # Include a state aggregate to prove it never becomes a county observation.
    old.append({**old[0], 'SUMLEV':'040', 'COUNTY':'000'})
    rows = ['<row><c r="C4"><v>2020</v></c><c r="D4"><v>2021</v></c><c r="E4"><v>2022</v></c>'
            '<c r="F4"><v>2023</v></c><c r="G4"><v>2024</v></c><c r="H4"><v>2025</v></c></row>']
    for i, name in enumerate(['.Autauga County, Alabama','Capitol Planning Region, Connecticut'],5):
        rows.append(f'<row><c r="A{i}" t="inlineStr"><is><t>{name}</t></is></c>' +
                    ''.join(f'<c r="{col}{i}"><v>{year*3}</v></c>' for col,year in zip('CDEFGH',range(2020,2026))) + '</row>')
    workbook = zipped({'xl/sharedStrings.xml':'<sst/>',
                       'xl/worksheets/sheet1.xml':'<worksheet xmlns="'+census.NS['s']+'"><sheetData>'+''.join(rows)+'</sheetData></worksheet>'})
    kml = '''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><ExtendedData><SchemaData>
    <SimpleData name="GEOID">01001</SimpleData><SimpleData name="ALAND">1000000</SimpleData>
    <SimpleData name="AWATER">0</SimpleData></SchemaData></ExtendedData><MultiGeometry><Polygon>
    <outerBoundaryIs><LinearRing><coordinates>-90,30,0 -89,30,0 -89,31,0 -90,30,0</coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>-89.9,30.1,0 -89.8,30.1,0 -89.8,30.2,0 -89.9,30.1,0</coordinates></LinearRing></innerBoundaryIs>
    </Polygon><Polygon><outerBoundaryIs><LinearRing><coordinates>-88,30 -87,30 -87,31 -88,30</coordinates></LinearRing></outerBoundaryIs>
    </Polygon></MultiGeometry></Placemark></Document></kml>'''
    payloads = [csv_bytes(old), csv_bytes(housing), csv_bytes(new), workbook, zipped({'counties.kml':kml})]
    return {url: body for (_,url),body in zip(census.SOURCES.values(),payloads)}


class CensusWorkflow(unittest.TestCase):
    def test_collection_cache_and_independent_corruption_detection(self):
        payloads = fixtures()
        calls = []
        def response(request, **kwargs):
            url = request.full_url; calls.append(url)
            body = io.BytesIO(payloads[url]);body.url=url;body.headers={}
            return body
        with TemporaryDirectory() as d, patch('download_data.urlopen',side_effect=response), redirect_stdout(io.StringIO()):
            args = ['--sources','census','--verify-downloads','--data-dir',d]
            self.assertEqual(download_data.main(args),0)
            root = Path(d)
            self.assertEqual(len(calls),5)
            with (root/'census_population/county_context_2010_2025.csv').open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows),22)
            first = rows[0]
            self.assertEqual((first['county_fips'],first['population'],first['housing_units']),('01001','20100','4020'))
            transition = next(r for r in rows if r['year']=='2020' and r['county_fips']=='01001')
            self.assertEqual((transition['housing_units'],transition['estimate_vintage']),('6060','2025'))
            self.assertEqual({r['map_2020_fips_present'] for r in rows if r['county_fips']=='09110'},{'false'})
            geo=json.loads((root/'census_boundaries/counties_2020_5m.geojson').read_text())
            self.assertEqual(list(map(len,geo['features'][0]['geometry']['coordinates'])),[2,1])
            self.assertEqual(download_data.main(args),0)
            self.assertEqual(len(calls),5, 'Second run must reuse verified raw files')
            with patch('download_data.download_records') as noaa:
                self.assertEqual(download_data.main(['--data-dir',d]),0)
                noaa.assert_called_once()
            self.assertEqual(len(calls),5, 'Default all-source mode must also reuse Census caches')
            # Change a derived value AND its manifest hash: source reconciliation must still reject it.
            table=root/'census_population/county_context_2010_2025.csv'
            table.write_text(table.read_text().replace(',20100,4020,',',20101,4020,'))
            manifest_path=root/'census_manifest_2010_2025.json'
            manifest=json.loads(manifest_path.read_text())
            manifest['context'].update(sha256=download_data.sha256(table),bytes=table.stat().st_size)
            download_data.atomic_json(manifest_path,manifest)
            result=verify(root,sources='census')
            self.assertEqual(result['status'],'failed')
            self.assertIn('differs from source estimates',result['errors'][0])

    def test_missing_housing_county_fails_without_zero_filling(self):
        payloads=fixtures()
        with TemporaryDirectory() as d:
            root=Path(d)
            for key,(relative,url) in census.SOURCES.items():
                p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payloads[url])
            housing=census.housing_workbook(root/census.SOURCES['housing_2025'][0])
            housing.pop('Capitol Planning Region, Connecticut')
            with patch('census_data.housing_workbook',return_value=housing), self.assertRaisesRegex(ValueError,'names differ'):
                census.context_rows(root,2020,2025,{'01001'})

    def test_supported_period_and_source_selection(self):
        self.assertEqual(set(census.selected_sources(2010,2019)),{'population_2020','housing_2020','counties_2020'})
        self.assertEqual(set(census.selected_sources(2020,2025)),{'population_2025','housing_2025','counties_2020'})
        with self.assertRaises(ValueError):census.context_rows(Path('/unused'),2009,2025,set())


if __name__ == '__main__':
    unittest.main()
