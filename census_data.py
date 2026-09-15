"""Small, pinned Census context sources; parsing uses only the standard library.

Network calls are supplied by download_data.py. Imports and parsing are read-only.
"""
from collections import Counter
from pathlib import Path
import csv
import json
import re
import xml.etree.ElementTree as ET
import zipfile

BASE = 'https://www2.census.gov/'
SOURCES = {
    'population_2020': ('census_population/raw/co-est2020-alldata.csv', BASE + 'programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv'),
    'housing_2020': ('census_population/raw/HU-EST2020_ALL.csv', BASE + 'programs-surveys/popest/datasets/2010-2020/housing/HU-EST2020_ALL.csv'),
    'population_2025': ('census_population/raw/co-est2025-alldata.csv', BASE + 'programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv'),
    'housing_2025': ('census_population/raw/CO-EST2025-HU.xlsx', BASE + 'programs-surveys/popest/tables/2020-2025/housing/totals/CO-EST2025-HU.xlsx'),
    'counties_2020': ('census_boundaries/raw/cb_2020_us_county_5m.zip', BASE + 'geo/tiger/GENZ2020/kml/cb_2020_us_county_5m.zip'),
}
FIELDS = ['year', 'county_fips', 'state_name', 'county_name', 'population', 'housing_units',
          'estimate_vintage', 'map_2020_fips_present']
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'k': 'http://www.opengis.net/kml/2.2'}


def county_csv(path):
    # Census bulk files use Windows-1252 (including accented county names).
    with Path(path).open(encoding='cp1252', newline='') as handle:
        rows = [row for row in csv.DictReader(handle) if row['SUMLEV'].zfill(3) == '050']
    result = {}
    for row in rows:
        code = row['STATE'].zfill(2) + row['COUNTY'].zfill(3)
        if not re.fullmatch(r'\d{5}', code) or code.endswith('000') or code in result:
            raise ValueError(f'Invalid/duplicate county FIPS: {code}')
        result[code] = row
    if not result:
        raise ValueError(f'No county records: {path}')
    return result


def housing_workbook(path):
    """Read the pinned Census single-sheet XLSX table, including sparse cells.

    No formulas are expected in the published estimate cells. Resolve shared
    strings and column references; never rely on physical cell positions.
    """
    with zipfile.ZipFile(path) as archive:
        strings = [''.join(si.itertext()) for si in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    values = []
    for row in sheet.findall('.//s:sheetData/s:row', NS):
        cells = {}
        for cell in row.findall('s:c', NS):
            if cell.find('s:f', NS) is not None:
                raise ValueError('Unexpected formula in Census housing workbook')
            value = cell.findtext('s:v', default='', namespaces=NS)
            if cell.get('t') == 's':
                value = strings[int(value)]
            elif cell.get('t') == 'inlineStr':
                value = ''.join(cell.find('s:is', NS).itertext())
            cells[re.sub(r'\d', '', cell.attrib['r'])] = value
        values.append(cells)
    headers = next((row for row in values if row.get('C') == '2020' and row.get('H') == '2025'), None)
    if not headers or [headers.get(c) for c in 'CDEFGH'] != list(map(str, range(2020, 2026))):
        raise ValueError('Census housing workbook year columns changed')
    result = {}
    for row in values:
        name = row.get('A', '')
        # Benson County lacks the leading dot in the published 2025 file.
        if ', ' not in name or not all(row.get(c, '').isdigit() for c in 'CDEFGH'):
            continue
        name = name.removeprefix('.')
        if name in result:
            raise ValueError(f'Duplicate housing county: {name}')
        result[name] = {int(headers[c]): nonnegative(row.get(c, '')) for c in 'CDEFGH'}
    if not result:
        raise ValueError('No county housing estimates in workbook')
    return result


def nonnegative(value):
    if not re.fullmatch(r'\d+', str(value)):
        raise ValueError(f'Missing or invalid Census count: {value!r}')
    return int(value)


def county_geojson(path):
    """Convert the small WGS84 KML map to GeoJSON, preserving holes and parts."""
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith('.kml')]
        if len(names) != 1:
            raise ValueError('Expected exactly one county KML file')
        root = ET.fromstring(archive.read(names[0]))
    features, seen = [], set()
    for place in root.findall('.//k:Placemark', NS):
        props = {item.attrib['name']: item.text for item in place.findall('.//k:SimpleData', NS)}
        code = props['GEOID']
        if not re.fullmatch(r'\d{5}', code) or code in seen:
            raise ValueError(f'Invalid/duplicate map FIPS: {code}')
        seen.add(code)
        polygons = []
        for polygon in place.findall('.//k:Polygon', NS):
            rings = []
            for boundary in ('outerBoundaryIs', 'innerBoundaryIs'):
                for coords in polygon.findall(f'k:{boundary}/k:LinearRing/k:coordinates', NS):
                    ring = [[float(v) for v in point.split(',')[:2]] for point in coords.text.split()]
                    if (len(ring) < 4 or ring[0] != ring[-1] or
                            not all(len(p) == 2 and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in ring)):
                        raise ValueError(f'Invalid map ring: {code}')
                    rings.append(ring)
            if not rings:
                raise ValueError(f'Empty map polygon: {code}')
            polygons.append(rings)
        if not polygons:
            raise ValueError(f'No polygons: {code}')
        # Keep source ALAND, rather than calculating area from a generalized map.
        props['ALAND'] = nonnegative(props['ALAND'])
        props['AWATER'] = nonnegative(props['AWATER'])
        features.append(dict(type='Feature', properties=props,
                             geometry=dict(type='MultiPolygon', coordinates=polygons)))
    if not features:
        raise ValueError('Empty county map')
    return dict(type='FeatureCollection', features=features)


def context_rows(data, start, end, map_codes):
    """One county/year row, with explicit release vintage and no geographic remapping."""
    if not 2010 <= start <= end <= 2025:
        raise ValueError('Census context supports 2010–2025')
    result = []
    for vintage, lo, hi in ((2020, 2010, 2019), (2025, 2020, 2025)):
        years = range(max(start, lo), min(end, hi) + 1)
        if not years:
            continue
        population = county_csv(data / SOURCES[f'population_{vintage}'][0])
        if vintage == 2020:
            housing = county_csv(data / SOURCES['housing_2020'][0])
            if set(population) != set(housing):
                raise ValueError('2010s population/housing county inventories differ')
        else:
            housing = housing_workbook(data / SOURCES['housing_2025'][0])
            names = {f"{r['CTYNAME']}, {r['STNAME']}" for r in population.values()}
            if len(names) != len(population) or names != set(housing):
                raise ValueError(f'2020s population/housing names differ: {names ^ set(housing)}')
        for code, pop in sorted(population.items()):
            for year in years:
                name = f"{pop['CTYNAME']}, {pop['STNAME']}"
                if vintage == 2020:
                    hu = housing[code]
                    if (hu['CTYNAME'], hu['STNAME']) != (pop['CTYNAME'], pop['STNAME']):
                        raise ValueError(f'Population/housing county names disagree: {code}')
                    count = nonnegative(hu[f'HUESTIMATE{year}'])
                else:
                    count = housing[name][year]
                result.append(dict(year=year, county_fips=code, state_name=pop['STNAME'],
                                   county_name=pop['CTYNAME'], population=nonnegative(pop[f'POPESTIMATE{year}']),
                                   housing_units=count, estimate_vintage=vintage,
                                   map_2020_fips_present=str(code in map_codes).lower()))
    return sorted(result, key=lambda r: (r['year'], r['county_fips']))


def selected_sources(start, end):
    return {key: value for key, value in SOURCES.items()
            if key == 'counties_2020' or key.endswith('2020') and start < 2020
            or key.endswith('2025') and end >= 2020}


def collect(data, start, end, download):
    from download_data import atomic_json, now, sha256
    data = Path(data)
    manifest_path = data / f'census_manifest_{start}_{end}.json'
    atomic_json(manifest_path, dict(status='in_progress', start_year=start, end_year=end, started_at=now()))
    sources = []
    for key, (relative, url) in selected_sources(start, end).items():
        path = download(url, data / relative, 'census_csv' if relative.endswith('.csv') else 'zip')
        sources.append(dict(id=key, path=relative, url=url, sha256=sha256(path), bytes=path.stat().st_size))
    geography = county_geojson(data / SOURCES['counties_2020'][0])
    codes = {f['properties']['GEOID'] for f in geography['features']}
    rows = context_rows(data, start, end, codes)
    table = data / 'census_population' / f'county_context_{start}_{end}.csv'
    part = table.with_name(table.name + '.part')
    try:
        with part.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        part.replace(table)
    finally:
        part.unlink(missing_ok=True)
    geo_path = data / 'census_boundaries' / 'counties_2020_5m.geojson'
    # Compact output avoids multiplying map size with coordinate indentation.
    part = geo_path.with_name(geo_path.name + '.part')
    try:
        part.write_text(json.dumps(geography, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        part.replace(geo_path)
    finally:
        part.unlink(missing_ok=True)
    def artifact(path, **extra):
        return dict(path=str(path.relative_to(data)), sha256=sha256(path), bytes=path.stat().st_size, **extra)
    missing = sorted({r['county_fips'] for r in rows if r['map_2020_fips_present'] == 'false'})
    manifest = dict(status='complete', completed_at=now(), start_year=start, end_year=end,
                    sources=sources, context=artifact(table, rows=len(rows)),
                    boundaries=artifact(geo_path, features=len(geography['features']), vintage=2020, scale='1:5,000,000'),
                    rows_by_year=dict(Counter(r['year'] for r in rows)), map_unmatched_fips=missing,
                    rules=['July 1 estimates; vintage 2020 for 2010–2019, vintage 2025 for 2020–2025.',
                           '50 states and DC estimates; map also includes Puerto Rico and other US territories.',
                           'Source-vintage county geographies; not remapped to historical event boundaries.',
                           'Map FIPS presence is only a code check, not proof of boundary compatibility.',
                           'Fixed 2020 generalized map for display; no event or path exposure joins.'])
    atomic_json(manifest_path, manifest)
    print(f'Census: {len(rows):,} county/year rows, {len(geography["features"]):,} map features; '
          f'{len(missing)} county codes absent from the 2020 map.', flush=True)
    return manifest
