"""SWDI Level III detections and IEM warning histories."""
import json
import re
from zipfile import ZipFile
from urllib.parse import urlencode

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.geometry import Point
from shapely import STRtree

from .common import Unavailable, numeric, project, stable_id, utc
from .checkpoints import SharedRows, BatchedRows
from .batching import radar_window, warning_window, warning_partition

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


def radar_query(cache, product, begin, finish, bounds, depth=0):
    """Subdivide capped queries instead of treating a capped response as complete."""
    url = (SWDI + f'json/{product}/{begin:%Y%m%d%H%M}:{finish:%Y%m%d%H%M}?' +
           urlencode({'bbox': ','.join(str(x) for x in bounds)}))
    payload, asset = cache.json(url)
    if 'result' not in payload or 'summary' not in payload:
        raise ValueError('SWDI error or unsupported response schema')
    count = int(payload['summary']['count'])
    if count < len(payload['result']):
        raise ValueError('Invalid SWDI result count')
    if count != len(payload['result']) or count >= 10000:
        if depth >= 20:
            raise ValueError('SWDI response still capped after subdivision')
        minutes = int((finish - begin).total_seconds() // 60)
        if minutes > 1:
            middle = begin + pd.Timedelta(minutes=minutes // 2)
            halves = [(begin, middle, bounds), (middle, finish, bounds)]
        else:
            x0, y0, x1, y1 = bounds
            if x1 - x0 >= y1 - y0:
                mid = round((x0 + x1) / 2, 6)
                halves = [(begin, finish, (x0, y0, mid, y1)), (begin, finish, (mid, y0, x1, y1))]
            else:
                mid = round((y0 + y1) / 2, 6)
                halves = [(begin, finish, (x0, y0, x1, mid)), (begin, finish, (x0, mid, x1, y1))]
        return [part for b, e, box in halves for part in radar_query(cache, product, b, e, box, depth + 1)]
    rows = cache.memo(('radar', asset), lambda: SharedRows(swdi_rows(payload, product, asset)))
    return [(rows, asset)]


def collect_radar(event, cache, config):
    center = Point(event['longitude'], event['latitude'])
    begin, finish, bounds = radar_window(event, config)
    query = getattr(cache, 'radar_plans', {}).get(event['tornado_id'], (begin, finish, bounds))
    tables = {'radar_detections': [], 'tornado_radar': []}
    cache.json(SWDI + 'json')
    cache.fetch(SWDI + 'csv/nx3tvs:inv', suffix='.txt')
    for product in config.swdi_products:
        parts = radar_query(cache, product, *query)
        # Bounding-box source rows retain API precision and native fields. Only
        # each event's original window enters its tables, not the entire batch.
        rows = {r['record_id']: r for part, _ in parts for r in part
                if bounds[0] <= r['longitude'] <= bounds[2] and bounds[1] <= r['latitude'] <= bounds[3]
                and begin <= utc(r['observed_at']) <= finish}
        rows = sorted(rows.values(), key=lambda r: r['record_id'])
        tables['radar_detections'].extend(rows)
        for row in rows:
            observed = utc(row['observed_at'])
            distance = project(Point(row['longitude'], row['latitude']), center.x, center.y).distance(Point(0, 0)) / 1000
            if begin <= observed <= finish and distance <= config.radar_radius_km:
                tables['tornado_radar'].append(dict(tornado_id=event['tornado_id'], record_id=row['record_id'],
                    distance_km=distance, available_at=(observed + pd.Timedelta(minutes=config.radar_latency_minutes)).isoformat(),
                    availability_basis='observation_plus_assumed_latency', query_asset_id=row['asset_id'],
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


def warning_input(path, asset, dates=None):
    """Normalize a warning partition, reproducing IEM's coalesce(issue, polygon_begin)."""
    frame = gpd.read_file(path).to_crs('EPSG:4326')
    if dates is not None:
        frame = frame.loc[frame.ISSUED.fillna(frame.POLY_BEG).str[:8].isin(dates)]
    return warning_frame(frame, asset)


def warning_frame(frame, asset):
    required = {'PROD_ID', 'WFO', 'PHENOM', 'ETN', 'VTEC_YR', 'POLY_BEG', 'INIT_ISS', 'geometry'}
    if not required <= set(frame.columns):
        raise ValueError('IEM warning schema changed')
    rows, details, by_warning = [], [], {}
    for position, (_, item) in enumerate(frame.iterrows()):
        raw = {k: (None if pd.isna(v) else v) for k, v in item.items() if k != 'geometry'}
        product_id = str(raw['PROD_ID'])
        if not re.match(r'^\d{12}-[A-Z0-9-]+$', product_id):
            raise ValueError('Invalid IEM product identifier')
        issued = pd.to_datetime(product_id[:12], format='%Y%m%d%H%M', utc=True)
        warning_id = f"vtec:{int(raw['VTEC_YR'])}:{raw['WFO']}:{raw['PHENOM']}:W:{int(raw['ETN']):04d}"
        record_id = stable_id('warning-update', [warning_id, product_id, str(raw['POLY_BEG']), item.geometry.wkt])
        rows.append(dict(record_id=record_id, warning_id=warning_id, product_id=product_id,
            phenomenon=raw['PHENOM'], issued_at=issued.isoformat(),
            original_issue_at=(pd.to_datetime(str(raw['INIT_ISS']), format='%Y%m%d%H%M', utc=True).isoformat()
                              if raw['INIT_ISS'] is not None else None),
            action=None, known_expiry_at=None, geometry_wkt=item.geometry.wkt,
            raw_json=json.dumps(raw, default=str, sort_keys=True), asset_id=asset, text_asset_id=None))
        details.append((str(raw['WFO']), raw['PHENOM'], raw['ETN']))
        by_warning.setdefault(warning_id, []).append(position)
    # Construct the read-only spatial index before sharing this frame with workers.
    index = frame.sindex
    return SharedRows(rows), details, by_warning, index


def warning_day(partition, begin, end):
    rows, details, _, _ = partition
    positions = []
    lower, upper = begin.strftime('%Y%m%d%H%M'), end.strftime('%Y%m%d%H%M')
    for i, row in enumerate(rows):
        raw = json.loads(row['raw_json'])
        stamp = raw.get('ISSUED') or raw['POLY_BEG']
        if lower <= stamp < upper:
            positions.append(i)
    subset = SharedRows([rows[i] for i in positions])
    by_warning = {}
    for i, row in enumerate(subset):
        by_warning.setdefault(row['warning_id'], []).append(i)
    return (subset, [details[i] for i in positions], by_warning,
            STRtree([wkt.loads(row['geometry_wkt']) for row in subset]))


def text_members(path):
    """Index exact product identities. Ambiguous/corrected names use single-product fallback."""
    found = {}
    with ZipFile(path) as archive:
        if len(archive.infolist()) >= 9999:
            return None
        for item in archive.infolist():
            match = re.fullmatch(r'([A-Z0-9]{3,6})_(\d{12})\.txt', item.filename)
            if not match:
                raise ValueError('Unexpected IEM text ZIP member')
            text = archive.read(item).decode('utf-8')
            header = re.search(r'^([A-Z]{4}\d{2}) ([A-Z]{4}) \d{6}(?: ([A-Z]{3}))?\s*$', text, re.M)
            if not header:
                continue
            pil, stamp = match.groups()
            product_id = f'{stamp}-{header[2]}-{header[1]}-{pil}'
            if header[3]:
                product_id += '-' + header[3]
            # ZIPs can repeat names. Never choose one silently.
            found[product_id] = None if product_id in found else text
    return found


def text_partition(cache, pil, begin, end):
    params = dict(sdate=begin.strftime('%Y-%m-%dT%H:%MZ'),
                  edate=end.strftime('%Y-%m-%dT%H:%MZ'), pil=pil, fmt='zip', limit=9999)
    path, asset = cache.fetch(IEM + 'cgi-bin/afos/retrieve.py?' + urlencode(params), suffix='.zip')
    members = cache.memo(('warning_text', asset), lambda: text_members(path))
    if members is not None:
        return [(members, asset)]
    minutes = int((end - begin).total_seconds() // 60)
    if minutes <= 1:
        raise ValueError('IEM text ZIP is capped even for a one-minute partition')
    middle = begin + pd.Timedelta(minutes=minutes // 2)
    return text_partition(cache, pil, begin, middle) + text_partition(cache, pil, middle, end)


def warning_text(product_id, cache):
    if getattr(cache, 'acquisition', 'event') == 'batch':
        stamp = pd.to_datetime(product_id[:12], format='%Y%m%d%H%M', utc=True)
        begin = stamp.floor('D')
        pil = product_id.split('-')[3][:3]
        days = getattr(cache, 'bulk_text_days', None)
        if pil in ('TOR', 'SVR', 'SVS') and (days is None or begin.strftime('%Y%m%d') in days):
            # One three-letter prefix per request: multiple prefixes mean exact
            # PIL matches in IEM and silently produce empty ZIPs.
            matches = [(members[product_id], asset) for members, asset in
                       text_partition(cache, pil, begin, begin + pd.Timedelta(days=1))
                       if product_id in members]
            if len(matches) == 1 and matches[0][0] is not None:
                text, asset = matches[0]
                _, member_asset = cache.member(asset, product_id, text)
                return text, member_asset
    path, asset = cache.fetch(IEM + 'api/1/nwstext/' + product_id, suffix='.txt')
    return path.read_text(), asset


def resolve_warning_text(product_id, cache, args):
    """Resolve same-minute product-ID collisions by exact office/phenomenon/ETN.

    The single-product endpoint can return another warning with the same public
    ID. A one-minute archive preserves all candidates, including duplicate ZIP
    names. Never attach a candidate without validating its full product identity
    and VTEC record; distinct matching texts remain an error.
    """
    text, asset = warning_text(product_id, cache)
    try:
        state = cache.memo(('vtec', asset, product_id, *args), lambda: parse_vtec(text, *args))
        return asset, state
    except ValueError:
        pass
    stamp, center, wmo, pil, *correction = product_id.split('-')
    begin = pd.to_datetime(stamp, format='%Y%m%d%H%M', utc=True)
    params = dict(sdate=begin.strftime('%Y-%m-%dT%H:%MZ'),
                  edate=(begin + pd.Timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%MZ'),
                  center=center, ttaaii=wmo, pil=pil, fmt='zip', limit=9999)
    path, parent = cache.fetch(IEM + 'cgi-bin/afos/retrieve.py?' + urlencode(params), suffix='.zip')
    matches = {}
    with ZipFile(path) as archive:
        if len(archive.infolist()) >= 9999:
            raise ValueError('IEM warning disambiguation archive is capped')
        for member in archive.infolist():
            if member.filename != f'{pil}_{stamp}.txt':
                continue
            candidate = archive.read(member).decode('utf-8')
            header = re.search(r'^([A-Z]{4}\d{2}) ([A-Z]{4}) \d{6}(?: ([A-Z]{3}))?\s*$', candidate, re.M)
            if not header or (header[1], header[2], header[3]) != (wmo, center, correction[0] if correction else None):
                continue
            try:
                state = parse_vtec(candidate, *args)
            except ValueError:
                continue
            matches[candidate] = state
    if len(matches) != 1:
        raise ValueError(f'Cannot uniquely resolve warning text for {product_id}, VTEC {args}')
    text, state = next(iter(matches.items()))
    _, asset = cache.member(parent, product_id + '/' + stable_id('vtec-text', [args, text]), text)
    return asset, state


def collect_warnings(event, cache, config):
    start = utc(event['start_utc'])
    begin, end = warning_window(start)
    query_begin, query_end = getattr(cache, 'warning_plans', {}).get(event['tornado_id'], (begin, end))
    params = dict(accept='shapefile', sts=query_begin.isoformat(), ets=query_end.isoformat(),
                  limitps=1, phenomena='TO,SV', significance='W,W', limit1=1, addsvs=1)
    path, asset = cache.fetch(IEM + 'cgi-bin/request/gis/watchwarn.py?' + urlencode(params), suffix='.zip')
    dates = getattr(cache, 'warning_dates', None) if getattr(cache, 'acquisition', 'event') == 'batch' else None
    partition = cache.memo(('warnings', asset), lambda: warning_input(path, asset, dates))
    rows, details, by_warning, index = (cache.memo(('warning_day', asset, begin, end), lambda: warning_day(partition, begin, end))
        if (query_begin, query_end) != (begin, end) else partition)
    center = Point(event['longitude'], event['latitude'])
    covering = set(index.query(center, predicate='covered_by'))
    relevant = {rows[i]['warning_id'] for i in covering}
    positions = sorted(i for key in relevant for i in by_warning[key])
    updates, links = [], []
    for position in positions:
        row = rows[position]
        args = details[position]
        text_asset, (action, expiry) = resolve_warning_text(row['product_id'], cache, args)
        updates.append(dict(row, action=action, known_expiry_at=expiry, text_asset_id=text_asset))
        links.append(dict(tornado_id=event['tornado_id'], record_id=row['record_id'], warning_id=row['warning_id'],
            covers_start=position in covering,
            association_method='warning_with_at_least_one_polygon_covering_reported_start',
            issue_lead_minutes=(start-utc(row['issued_at'])).total_seconds()/60))
    return {'warning_updates': BatchedRows(rows, updates), 'tornado_warnings': links}
