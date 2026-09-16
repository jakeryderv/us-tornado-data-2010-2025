"""SWDI Level III detections and IEM warning histories."""
import json
import re
from urllib.parse import urlencode

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.geometry import Point

from .common import Unavailable, numeric, project, stable_id, utc

SWDI = 'https://www.ncei.noaa.gov/swdiws/'
IEM = 'https://mesonet.agron.iastate.edu/'
def swdi_rows(payload, product, asset_id):
    if 'result' not in payload or 'summary' not in payload:
        raise ValueError('SWDI error or unsupported response schema')
    source = payload['result']
    if int(payload['summary']['count']) != len(source) or len(source) >= 10000:
        raise ValueError('SWDI response may be truncated; subdivide this query')
    rows = []
    for raw in source:
        point = wkt.loads(raw['SHAPE'])
        stamp = utc(raw['ZTIME'])
        if point.geom_type != 'Point' or pd.isna(stamp):
            raise ValueError('Invalid SWDI point/time')
        rows.append(dict(record_id=stable_id(product, raw), product=product,
                         observed_at=stamp.isoformat(), radar_id=raw['WSR_ID'], cell_id=raw['CELL_ID'],
                         longitude=point.x, latitude=point.y, geometry_wkt=point.wkt,
                         max_shear_per_s=None if numeric(raw.get('MAX_SHEAR')) is None else numeric(raw['MAX_SHEAR']) / 1000,
                         velocity_difference_knots=numeric(raw.get('MXDV', raw.get('LL_DV'))),
                         rotation_velocity_knots=numeric(raw.get('MAX_RV_KTS')),
                         max_reflectivity_dbz=numeric(raw.get('MAX_REFLECT')), vil_kg_m2=numeric(raw.get('VIL')),
                         range_nautical_miles=numeric(raw.get('RANGE')),
                         raw_json=json.dumps(raw, sort_keys=True), asset_id=asset_id))
    return rows


def collect_radar(event, cache, config):
    start = utc(event['start_utc'])
    center = Point(event['longitude'], event['latitude'])
    region = project(project(center, center.x, center.y).buffer(config.radar_radius_km * 1000),
                     center.x, center.y, inverse=True)
    begin = start - pd.Timedelta(minutes=config.radar_before_minutes)
    finish = start + pd.Timedelta(minutes=config.radar_after_minutes)
    tables = {'radar_detections': [], 'tornado_radar': []}
    cache.json(SWDI + 'json')
    cache.fetch(SWDI + 'csv/nx3tvs:inv', suffix='.txt')
    for product in config.swdi_products:
        url = (SWDI + f'json/{product}/{begin:%Y%m%d%H%M}:{finish:%Y%m%d%H%M}?' +
               urlencode({'bbox': ','.join(str(round(x, 6)) for x in region.bounds)}))
        payload, asset = cache.json(url)
        rows = swdi_rows(payload, product, asset)
        tables['radar_detections'].extend(rows)
        for row in rows:
            observed = utc(row['observed_at'])
            distance = project(Point(row['longitude'], row['latitude']), center.x, center.y).distance(Point(0, 0)) / 1000
            if begin <= observed <= finish and distance <= config.radar_radius_km:
                tables['tornado_radar'].append(dict(tornado_id=event['tornado_id'], record_id=row['record_id'],
                    distance_km=distance, available_at=(observed + pd.Timedelta(minutes=config.radar_latency_minutes)).isoformat(),
                    availability_basis='observation_plus_assumed_latency', query_asset_id=asset,
                    association_method='fixed_radius_around_reported_start; not confirmed storm identity'))
    return tables


def parse_vtec(text, wfo, phenomenon, etn):
    pattern = r'/[OTEX]\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.W\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/'
    matches = [m for m in re.findall(pattern, text) if m[1].endswith(wfo) and m[2] == phenomenon and int(m[3]) == int(etn)]
    # Partial county cancellations share a product with the continuing warning.
    # IEM's updated storm-based polygon describes the remaining warned area.
    # Retain the live action when it is unambiguous; a wholly cancelled warning
    # has only CAN/EXP entries. Original text preserves all county actions.
    live = [m for m in matches if m[0] not in ('CAN', 'EXP')]
    candidates = live or matches
    states = {(m[0], m[5]) for m in candidates}
    if len(states) != 1:
        raise ValueError('Cannot identify a unique warning VTEC record in original product')
    action, ends = states.pop()
    return action, pd.to_datetime(ends, format='%y%m%dT%H%MZ', utc=True).isoformat()


def collect_warnings(event, cache, config):
    start = utc(event['start_utc'])
    # Include prior-day issuance for events just after midnight.
    begin = start.floor('D') - pd.Timedelta(days=1)
    end = start.floor('D') + pd.Timedelta(days=1)
    params = dict(accept='shapefile', sts=begin.isoformat(), ets=end.isoformat(),
                  limitps=1, phenomena='TO,SV', significance='W,W', limit1=1, addsvs=1)
    path, asset = cache.fetch(IEM + 'cgi-bin/request/gis/watchwarn.py?' + urlencode(params), suffix='.zip')
    frame = cache.memo(('warnings',asset),lambda:gpd.read_file(path).to_crs('EPSG:4326'))
    required = {'PROD_ID', 'WFO', 'PHENOM', 'ETN', 'VTEC_YR', 'POLY_BEG', 'INIT_ISS', 'geometry'}
    if not required <= set(frame.columns):
        raise ValueError('IEM warning schema changed')
    result = {'warning_updates': [], 'tornado_warnings': []}
    center = Point(event['longitude'], event['latitude'])
    def identity(raw):
        return f"vtec:{int(raw['VTEC_YR'])}:{raw['WFO']}:{raw['PHENOM']}:W:{int(raw['ETN']):04d}"
    relevant = {identity(row) for _, row in frame.iterrows() if row.geometry.covers(center)}
    for _, item in frame.iterrows():
        raw = {k: (None if pd.isna(v) else v) for k, v in item.items() if k != 'geometry'}
        product_id = str(raw['PROD_ID'])
        if not re.match(r'^\d{12}-[A-Z0-9-]+$', product_id):
            raise ValueError('Invalid IEM product identifier')
        issued = pd.to_datetime(product_id[:12], format='%Y%m%d%H%M', utc=True)
        warning_id = identity(raw)
        record_id = stable_id('warning-update', [warning_id, product_id, str(raw['POLY_BEG']), item.geometry.wkt])
        intersects = item.geometry.covers(center)
        valid_until, action, text_asset = None, None, None
        # Original messages resolve issue-time expiry rather than using the
        # retrospectively shortened EXPIRED/POLY_END columns.
        if warning_id in relevant:
            txt, text_asset = cache.fetch(IEM + 'api/1/nwstext/' + product_id, suffix='.txt')
            action, valid_until = parse_vtec(txt.read_text(), str(raw['WFO']), str(raw['PHENOM']), raw['ETN'])
        row = dict(record_id=record_id, warning_id=warning_id, product_id=product_id,
                   phenomenon=raw['PHENOM'], issued_at=issued.isoformat(),
                   original_issue_at=(pd.to_datetime(str(raw['INIT_ISS']), format='%Y%m%d%H%M', utc=True).isoformat()
                                      if raw['INIT_ISS'] is not None else None),
                   action=action, known_expiry_at=valid_until, geometry_wkt=item.geometry.wkt,
                   raw_json=json.dumps(raw, default=str, sort_keys=True), asset_id=asset, text_asset_id=text_asset)
        result['warning_updates'].append(row)
        if warning_id in relevant:
            result['tornado_warnings'].append(dict(tornado_id=event['tornado_id'], record_id=record_id,
                warning_id=warning_id, covers_start=intersects,
                association_method='warning_with_at_least_one_polygon_covering_reported_start',
                issue_lead_minutes=(start-issued).total_seconds()/60))
    return result
