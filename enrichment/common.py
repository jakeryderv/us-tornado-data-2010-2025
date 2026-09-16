"""Content-verified HTTP cache, stable keys, and shared spatial conventions."""
from dataclasses import asdict, dataclass
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import pandas as pd
from pyproj import CRS, Transformer
from shapely.ops import transform


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stable_id(prefix, value):
    return prefix + ':' + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + '\n')
    temp.replace(path)


def public_url(url):
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode([(k, v) for k, v in parse_qsl(parts.query)
                                                     if k.lower() not in {'key', 'token', 'api_key'}])))


class Unavailable(Exception):
    """Known source/region/date gap, distinct from a failed request."""


class BudgetExceeded(Exception):
    pass


class CacheLimitExceeded(Exception):
    pass


@dataclass(frozen=True)
class Config:
    radar_before_minutes: int = 60
    radar_after_minutes: int = 60
    radar_radius_km: float = 20
    radar_latency_minutes: int = 5
    era5_tile_degrees: int = 5
    era5_max_distance_km: float = 25
    fallback_buffer_m: float = 500
    acs_year_lag: int = 1
    nlcd_year_lag: int = 1
    swdi_products: tuple = ('nx3tvs', 'nx3mda', 'nx3structure')

    def validate(self):
        if any(v <= 0 for k, v in asdict(self).items() if isinstance(v, (float, int))):
            raise ValueError('All durations, distances and vintage lags must be positive')
        if self.acs_year_lag != 1:
            raise ValueError('ACS first implementation uses event-year minus one; preserve this documented convention')


class Cache:
    """Cache identity excludes secrets; every hit is checked against its digest.

    A completed transport is not a completed source query. Adapters must validate
    payload schemas before the job is marked complete. Bodies stay immutable.
    """
    def __init__(self, root, *, timeout=90, max_bytes=5_000_000_000,
                 storage='archive', max_cache_bytes=5_000_000_000):
        if storage not in ('compact', 'cache', 'archive') or max_cache_bytes<=0:
            raise ValueError('Invalid storage policy or cache size')
        self.root = Path(root)
        self.timeout, self.max_bytes = timeout, max_bytes
        self.storage, self.max_cache_bytes = storage, max_cache_bytes
        self.downloaded = 0
        self.assets = {}
        self.touched = set()
        self.entries = {}
        self.evicted_bytes = 0
        self.memoized = OrderedDict()
        for meta_path in self.root.glob('http_*.*.json'):
            meta=json.loads(meta_path.read_text())
            if 'request_id' not in meta or 'sha256' not in meta:continue
            path=meta_path.with_suffix('')
            if path.exists() and self.retention(meta['url'],path.suffix)=='optional':
                self.entries[path]=dict(bytes=path.stat().st_size, last_use=path.stat().st_mtime,
                                        evictable=bool(meta.get('extraction_committed_at')))

    def memo(self, key, build):
        """Two decoded source inputs, keyed by content digest, shared by events."""
        if key not in self.memoized:self.memoized[key]=build()
        self.memoized.move_to_end(key)
        while len(self.memoized)>2:self.memoized.popitem(last=False)
        return self.memoized[key]

    def retention(self, url, suffix):
        from .storage import supporting_file
        return 'required' if self.storage=='archive' or supporting_file(url,suffix) else 'optional'

    def make_room(self, incoming=0):
        if self.storage=='archive':return
        used=sum(e['bytes'] for e in self.entries.values())
        if used+incoming<=self.max_cache_bytes:return
        for path,entry in sorted(self.entries.items(),key=lambda x:x[1]['last_use']):
            if used+incoming<=self.max_cache_bytes:break
            if not entry['evictable']:continue
            path.unlink(missing_ok=True)
            used-=entry['bytes'];self.evicted_bytes+=entry['bytes']
            del self.entries[path]
        if used+incoming>self.max_cache_bytes:
            raise CacheLimitExceeded('Active/uncommitted artifacts exceed --cache-gb; increase the cache limit')

    def commit(self, assets):
        """Called only after a verified extraction/outcome checkpoint is durable."""
        for asset in assets:
            path=self.root/Path(asset['path']).name
            entry=self.entries.get(path)
            if entry is None:continue
            meta_path=path.with_suffix(path.suffix+'.json')
            meta=json.loads(meta_path.read_text())
            if meta['sha256']!=asset['sha256']:
                raise ValueError('Source changed before extraction checkpoint')
            meta['extraction_committed_at']=now()
            atomic_json(meta_path,meta)
            entry['evictable']=True
        self.make_room()

    def finish(self):
        if self.storage=='compact':
            for path,entry in list(self.entries.items()):
                if entry['evictable']:
                    path.unlink(missing_ok=True)
                    self.evicted_bytes+=entry['bytes']
                    del self.entries[path]

    def fetch(self, url, *, suffix='.bin', headers=None, identity_url=None, request_metadata=None):
        headers = headers or {}
        clean = public_url(identity_url or url)
        request_id = stable_id('http', [clean, headers.get('Range')])
        path = self.root / (request_id.replace(':', '_') + suffix)
        meta_path = path.with_suffix(path.suffix + '.json')
        if path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if path.stat().st_size != meta['bytes'] or digest(path) != meta['sha256']:
                raise ValueError(f'Corrupt cache: {path}; remove this cache entry before retrying')
            self.assets[meta['asset_id']] = meta
            meta['retention']=self.retention(clean,suffix)
            self.touched.add(meta['asset_id'])
            if path in self.entries:
                self.entries[path].update(last_use=time.time(),evictable=False)
            return path, meta['asset_id']
        self.root.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + '.part')
        # Resolve queued CDS downloads only on a miss. Signed download URLs
        # never become persistent identifiers or appear in provenance/errors.
        remote_url=url() if callable(url) else url
        for attempt in range(3):
            try:
                with urlopen(Request(remote_url, headers={'User-Agent': 'us-tornado-data/optional-enrichment', **headers}),
                             timeout=self.timeout) as response, temp.open('wb') as out:
                    if 'Range' in headers and response.status != 206:
                        raise ValueError('Server ignored byte range; refusing a full model download')
                    if 'Range' in headers:
                        actual = response.headers.get('Content-Range', '').split('/')[0]
                        if actual != headers['Range'].replace('bytes=', 'bytes '):
                            raise ValueError('Server returned a different byte range')
                    expected = response.headers.get('Content-Length')
                    if expected and self.downloaded + int(expected) > self.max_bytes:
                        raise BudgetExceeded('Download budget reached; raise --max-download-gb to resume')
                    optional=self.retention(clean,suffix)=='optional'
                    if optional:self.make_room(int(expected or 0))
                    received=0
                    for block in iter(lambda: response.read(1024 * 1024), b''):
                        self.downloaded += len(block)
                        if self.downloaded > self.max_bytes:
                            raise BudgetExceeded('Download budget reached; partial response not committed')
                        received+=len(block)
                        if optional:self.make_room(received)
                        out.write(block)
                    out.flush()
                    if expected and temp.stat().st_size != int(expected):
                        raise ValueError('Truncated HTTP response')
                    with temp.open('rb') as check:
                        magic = check.read(4)
                    expected_magic = {'.zip': (b'PK\x03\x04', b'PK\x05\x06'),
                                      '.tif': (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'),
                                      '.grib2': (b'GRIB',), '.grib': (b'GRIB',)}
                    if suffix in expected_magic and magic not in expected_magic[suffix]:
                        raise ValueError(f'Expected {suffix} binary payload from {clean}')
                    meta = dict(request_id=request_id, url=clean, final_url=clean if identity_url else public_url(response.url),
                                retrieved_at=now(), bytes=temp.stat().st_size, sha256=digest(temp),
                                etag=response.headers.get('ETag'), last_modified=response.headers.get('Last-Modified'),
                                content_range=response.headers.get('Content-Range'), request_range=headers.get('Range'))
                    if request_metadata is not None:meta['request_metadata']=request_metadata
                meta['asset_id'] = stable_id('asset', [request_id, meta['sha256']])
                meta['path'] = str(path.resolve())
                meta['retention']=self.retention(clean,suffix)
                temp.replace(path)
                atomic_json(meta_path, meta)
                self.assets[meta['asset_id']] = meta
                self.touched.add(meta['asset_id'])
                if optional:self.entries[path]=dict(bytes=meta['bytes'],last_use=time.time(),evictable=False)
                return path, meta['asset_id']
            except HTTPError as exc:
                temp.unlink(missing_ok=True)
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    # Never include a credential-bearing request URL in errors.
                    raise RuntimeError(f'HTTP {exc.code} for {clean}') from None
                time.sleep(1 + attempt)
            except Exception:
                temp.unlink(missing_ok=True)
                raise

    def json(self, url):
        path, asset = self.fetch(url, suffix='.json')
        try:
            return json.loads(path.read_text()), asset
        except ValueError:
            # Error HTML (notably the Census Missing Key page) must not poison
            # a future authenticated retry using the same public cache identity.
            path.unlink(missing_ok=True)
            path.with_suffix('.json.json').unlink(missing_ok=True)
            self.assets.pop(asset, None)
            self.touched.discard(asset)
            self.entries.pop(path,None)
            raise ValueError(f'Expected JSON from {public_url(url)}') from None


def project(geometry, lon, lat, inverse=False):
    local = CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m')
    transformer = Transformer.from_crs(local if inverse else 'EPSG:4326',
                                       'EPSG:4326' if inverse else local, always_xy=True)
    return transform(transformer.transform, geometry)


def numeric(value):
    try:
        value = float(value)
        return value if pd.notna(value) and value not in (-999, -9999, -99999) else None
    except (ValueError, TypeError):
        return None


def utc(value):
    return pd.to_datetime(value, utc=True, errors='coerce')
