"""Separate Census source tables and native Annual NLCD path-area extracts."""
import json
import math
import os
import re
from urllib.parse import urlencode, urljoin

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.ops import transform

from .common import Unavailable, project, stable_id

ACS_VARIABLES = {
    'B01003_001': 'population', 'B25001_001': 'housing_units',
    'B25024_010': 'mobile_home_units',
}
TIGER = 'https://www2.census.gov/geo/tiger/'
NLCD_PRODUCTS = {'land_cover': 'Land-Cover-Native', 'impervious': 'Fractional-Impervious-Surface-Native'}
NLCD_CLASSES = (11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95)


def acs_rows(payload, year, asset):
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError('ACS did not return a header/rows array')
    frame = pd.DataFrame(payload[1:], columns=payload[0])
    required = {'NAME', 'state', 'county', 'tract', *[v+s for v in ACS_VARIABLES for s in ('E','M')]}
    if not required <= set(frame.columns):
        raise ValueError('Missing ACS estimate/MOE/geography fields')
    rows = []
    for raw in frame.to_dict('records'):
        geoid = raw['state'] + raw['county'] + raw['tract']
        if not re.fullmatch(r'\d{11}', geoid):
            raise ValueError('Invalid ACS tract GEOID')
        row = dict(record_id=f'acs5:{year}:{geoid}', geoid=geoid, period_start=year-4,
                   period_end=year, geography_vintage=year, name=raw['NAME'],
                   asset_id=asset, raw_json=json.dumps(raw, sort_keys=True))
        for variable, name in ACS_VARIABLES.items():
            for suffix, label in [('E',name), ('M',name+'_moe')]:
                value = pd.to_numeric(raw[variable+suffix], errors='coerce')
                # Census negative API sentinels are not real population or MOE.
                row[label] = int(value) if pd.notna(value) and value >= 0 else None
        rows.append(row)
    return rows


def collect_acs(event, cache, config):
    key = os.environ.get('CENSUS_API_KEY', '').strip()
    if not key:
        raise RuntimeError('CENSUS_API_KEY is required; set it in ignored .env and rerun')
    year = event['year'] - config.acs_year_lag
    rows = []
    for state in event['area_states']:
        params = {'get': ','.join(['NAME']+[v+s for v in ACS_VARIABLES for s in ('E','M')]),
                  'for': 'tract:*', 'in': 'state:'+state, 'key': key}
        payload, asset = cache.json(f'https://api.census.gov/data/{year}/acs/acs5?' + urlencode(params))
        rows.extend(cache.memo(('acs',year,asset),lambda:acs_rows(payload, year, asset)))
    return {'acs_tracts': rows}


def tiger_urls(year, state, counties, cache):
    if year >= 2011:
        return [f'{TIGER}TIGER{year}/TRACT/tl_{year}_{state}_tract.zip']
    if year == 2010:
        return [f'{TIGER}TIGER2010/TRACT/2010/tl_2010_{state}_tract10.zip']
    if year != 2009:
        raise Unavailable('Unsupported TIGER vintage')
    if not counties:
        raise Unavailable('2009 county ZIP selection needs an accepted county link; no modern boundary substitution')
    root = TIGER + 'TIGER2009/'
    index, _ = cache.fetch(root, suffix='.html')
    states = [x for x in re.findall(r'href="([^"]+/)"', index.read_text()) if x.startswith(state+'_')]
    if len(states) != 1:
        raise ValueError('Cannot identify TIGER 2009 state directory')
    state_url = urljoin(root, states[0])
    index, _ = cache.fetch(state_url, suffix='.html')
    directories = re.findall(r'href="([^"]+/)"', index.read_text())
    urls = []
    for county in counties:
        selected = [x for x in directories if x.startswith(county+'_')]
        if len(selected) != 1:
            raise ValueError(f'Cannot identify TIGER 2009 county {county}')
        folder = urljoin(state_url, selected[0])
        index, _ = cache.fetch(folder, suffix='.html')
        zips = [x for x in re.findall(r'href="([^"]+\.zip)"', index.read_text()) if '_tract00.' in x]
        if len(zips) != 1:
            raise ValueError('Cannot identify TIGER 2009 tract ZIP')
        urls.append(urljoin(folder, zips[0]))
    return urls


def collect_tracts(event, cache, config):
    year = event['year'] - config.acs_year_lag
    polygon = wkt.loads(event['area_wkt'])
    tables = {'tract_boundaries': [], 'tornado_tracts': []}
    lon, lat = event['longitude'], event['latitude']
    area = project(polygon, lon, lat)
    # Keep complete state source ZIPs; normalized output contains intersecting
    # tracts. Never infer matching geometry from a different vintage.
    for state in event['area_states']:
        counties = [x for x in event['county_fips'] if x.startswith(state)]
        for url in tiger_urls(year, state, counties, cache):
            path, asset = cache.fetch(url, suffix='.zip')
            frame = cache.memo(('tiger',asset),lambda:gpd.read_file(path).to_crs('EPSG:4326'))
            geoid_field = next((n for n in ('GEOID', 'GEOID10', 'CTIDFP00') if n in frame), None)
            if geoid_field is None:
                raise ValueError('TIGER tract identifier field missing')
            frame = frame.loc[frame.geometry.intersects(polygon)]
            for _, item in frame.iterrows():
                geoid = str(item[geoid_field])
                if not re.fullmatch(r'\d{11}', geoid):
                    raise ValueError('Invalid TIGER tract GEOID')
                raw = {k: None if pd.isna(v) else v for k,v in item.items() if k != 'geometry'}
                tract = project(item.geometry, lon, lat)
                if not tract.is_valid or tract.area <= 0:
                    raise ValueError('Invalid intersecting tract geometry')
                overlap = area.intersection(tract).area
                if overlap <= 0:
                    continue
                record_id = f'tiger:{year}:{geoid}'
                tables['tract_boundaries'].append(dict(record_id=record_id, geoid=geoid,
                    vintage=year, geometry_wkt=item.geometry.wkt, raw_json=json.dumps(raw, sort_keys=True, default=str), asset_id=asset))
                tables['tornado_tracts'].append(dict(tornado_id=event['tornado_id'], area_id=event['area_id'],
                    tract_id=record_id, acs_id=f'acs5:{year}:{geoid}', geoid=geoid, vintage=year,
                    intersection_m2=overlap, tract_area_m2=tract.area, intersection_fraction=overlap/tract.area,
                    area_fraction=overlap/area.area, method='uniform_within_tract_area_weighting',
                    retrospective=True))
    return tables


def raster_summary(path, polygon, product):
    import rasterio
    from rasterio.features import geometry_mask
    with rasterio.open(path) as src:
        if src.count != 1 or src.crs is None or abs(abs(src.transform.a)-30) > 0.01 or abs(abs(src.transform.e)-30) > 0.01:
            raise ValueError('NLCD must be a one-band native 30 m numeric raster')
        if src.crs.to_epsg() != 5070 or src.transform.b != 0 or src.transform.d != 0:
            raise ValueError('NLCD requires the unrotated EPSG:5070 grid')
        geom = transform(Transformer.from_crs('EPSG:4326', src.crs, always_xy=True).transform, polygon)
        mask = geometry_mask([geom], out_shape=src.shape, transform=src.transform, invert=True, all_touched=False)
        values = src.read(1, masked=True)
        valid = mask & ~np.ma.getmaskarray(values)
        if product == 'land_cover':
            valid &= np.isin(values.data, NLCD_CLASSES)
        else:
            valid &= (values.data >= 0) & (values.data <= 100)
        selected = values.data[valid]
        row = dict(pixel_count=int(mask.sum()), valid_pixel_count=int(valid.sum()), pixel_area_m2=900,
                   raster_crs=src.crs.to_string(), raster_transform=list(src.transform)[:6],
                   pixel_rule='center_within_polygon; no resampling', mean_impervious_percent=None)
        if product == 'land_cover':
            for c in NLCD_CLASSES:
                row[f'class_{c}_pixels'] = int((selected == c).sum())
        elif selected.size:
            row['mean_impervious_percent'] = float(selected.mean())
        return row


def collect_nlcd(event, cache, config):
    polygon = wkt.loads(event['area_wkt'])
    if not (-125 <= event['longitude'] <= -66 and 24 <= event['latitude'] <= 50):
        raise Unavailable('Annual NLCD selected products cover CONUS only')
    year = event['year'] - config.nlcd_year_lag
    projected = transform(Transformer.from_crs('EPSG:4326', 'EPSG:5070', always_xy=True).transform, polygon)
    # Snap to the documented native grid; avoid interpolation of class labels.
    origins = (-2415585.0, 164805.0)
    x0,y0,x1,y1 = projected.bounds
    bounds = (origins[0]+math.floor((x0-origins[0])/30)*30,
              origins[1]+math.floor((y0-origins[1])/30)*30,
              origins[0]+math.ceil((x1-origins[0])/30)*30,
              origins[1]+math.ceil((y1-origins[1])/30)*30)
    rows = []
    for product, layer in NLCD_PRODUCTS.items():
        service = f'https://dmsdata.cr.usgs.gov/geoserver/mrlc_{layer}_conus_year_data/wcs'
        coverage = f'mrlc_{layer}_conus_year_data:{layer}_conus_year_data'
        description, description_asset = cache.fetch(service+'?'+urlencode(dict(
            service='WCS', version='2.0.1', request='DescribeCoverage', coverageId=coverage.replace(':','__'))), suffix='.xml')
        if f'{year}-01-01T00:00:00' not in description.read_text():
            raise Unavailable(f'NLCD service does not list vintage {year}')
        params = dict(service='WCS', version='1.0.0', request='GetCoverage', coverage=coverage,
                      format='GeoTIFF', bbox=','.join(map(str,bounds)), crs='EPSG:5070',
                      resx=30, resy=30, time=f'{year}-01-01T00:00:00Z')
        path, asset = cache.fetch(service+'?'+urlencode(params), suffix='.tif')
        stats = raster_summary(path, polygon, product)
        rows.append(dict(record_id=stable_id('nlcd', [event['area_id'], year, product]),
                         tornado_id=event['tornado_id'], area_id=event['area_id'], product=product,
                         year=year, service_coverage=coverage, asset_id=asset,
                         description_asset_id=description_asset, retrospective=True, **stats))
    return {'nlcd_samples': rows}
