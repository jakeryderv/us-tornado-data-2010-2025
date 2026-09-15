"""Collect NOAA Event Footprint Catalog annual snapshots without cloud credentials.

Source bytes, GCS generations, upstream MD5 checksums, and local SHA-256 hashes
are retained. Footprints are damage regions, not a unique tornado catalog.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
import base64
import hashlib
import json

BUCKET = 'noaa-ncei-ipg'
PREFIX = 'datasets/event-catalog/'
BASE = f'https://storage.googleapis.com/{BUCKET}/{PREFIX}'
INVENTORY_URL = (f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o?'
                 + urlencode({'prefix': PREFIX+'tornado/', 'maxResults': 1000}))


def validate_collection(value, year):
    if value.get('type') != 'FeatureCollection' or not isinstance(value.get('features'), list):
        raise ValueError('EFC payload must be a GeoJSON FeatureCollection')
    seen, counts = set(), Counter(DAT=0, SED=0)
    quality = Counter(width_placeholder=0, width_zero=0, missing_geometry=0,
                      utc_year_differs=0, with_parents=0, with_children=0)
    for feature in value['features']:
        props = feature['properties']
        source, oid = props['source'], props['objectid']
        if source not in ('DAT', 'SED') or not isinstance(oid, int) or isinstance(oid, bool) or oid < 0:
            raise ValueError('Invalid EFC source or object ID')
        if (source, oid) in seen:
            raise ValueError('EFC source/object IDs must be unique within each annual file')
        seen.add((source, oid)); counts[source] += 1
        date = datetime.fromisoformat(props['stormdate'].replace('Z', '+00:00'))
        # Annual source partitions can use local/convective dates across UTC New Year.
        if date.utcoffset() != timedelta(0) or not (
                datetime(year,1,1,tzinfo=timezone.utc)-timedelta(days=1) <= date
                < datetime(year+1,1,1,tzinfo=timezone.utc)+timedelta(days=1)):
            raise ValueError('EFC storm date outside annual partition boundary tolerance or not UTC')
        geometry = feature.get('geometry')
        if geometry is not None and geometry.get('type') not in ('Polygon', 'MultiPolygon'):
            raise ValueError('Unexpected EFC geometry type')
        for name in ['parents', 'children']:
            values = props.get(name, [])
            if not isinstance(values, list) or any(not isinstance(x,int) or isinstance(x,bool) for x in values):
                raise ValueError('EFC relationship IDs must be integer arrays')
        quality['width_placeholder'] += props.get('width') == 0.99
        quality['width_zero'] += props.get('width') == 0
        quality['missing_geometry'] += not bool(geometry and geometry.get('coordinates'))
        quality['utc_year_differs'] += date.year != year
        quality['with_parents'] += bool(props.get('parents'))
        quality['with_children'] += bool(props.get('children'))
    return dict(rows=len(seen), source_counts=dict(counts), quality=dict(quality))


def verify_object(path, item):
    body = Path(path).read_bytes()
    if len(body) != int(item['size']) or base64.b64encode(hashlib.md5(body).digest()).decode() != item['md5Hash']:
        raise ValueError(f'EFC upstream size/MD5 mismatch: {path}')


def collect(data, start, end, download):
    from download_data import now, sha256
    data = Path(data)
    def artifact(path):
        return dict(path=path.relative_to(data).as_posix(), bytes=path.stat().st_size, sha256=sha256(path))
    inventory_path = download(INVENTORY_URL, data/'event_footprints/inventory.json', 'json')
    inventory = json.loads(inventory_path.read_text())
    if inventory.get('nextPageToken'):
        raise ValueError('EFC inventory pagination changed; refusing an incomplete inventory')
    items = {i['name']: i for i in inventory['items']}
    if len(items) != len(inventory['items']):
        raise ValueError('Duplicate EFC inventory names')
    documents = [artifact(download(BASE+name, data/'event_footprints'/name, 'text'))
                 for name in ['README.md', 'DOWNLOAD.md']]
    outputs = []
    for year in range(start,end+1):
        name = f'{PREFIX}tornado/{year}_tornado_footprint.geojson'
        if name not in items:
            raise ValueError(f'EFC annual file unavailable: {year}')
        item = items[name]
        generation = str(item['generation'])
        if not generation.isdigit():
            raise ValueError('Invalid EFC object generation')
        url = f'https://storage.googleapis.com/{BUCKET}/{name}?generation={generation}'
        path = download(url, data/'event_footprints'/f'{year}_tornado_footprint.geojson', 'json')
        verify_object(path,item)
        checked = validate_collection(json.loads(path.read_text()),year)
        outputs.append(dict(source='EFC',table='footprints',year=year,**artifact(path),**checked,
                            url=url,object_name=name,generation=generation,md5_base64=item['md5Hash'],
                            upstream_updated_at=item['updated'],year_counts={year:checked['rows']},
                            date_field='annual source partition; stormdate is UTC'))
        print(f'EFC {year}: {checked["rows"]:,} footprints; {checked["source_counts"]}',flush=True)
    return dict(status='complete',completed_at=now(),start_year=start,end_year=end,
                inventory=artifact(inventory_path),documents=documents,outputs=outputs,
                scope='Annual tornado footprints derived from DAT and Storm Events; no individual survey points or photos')
