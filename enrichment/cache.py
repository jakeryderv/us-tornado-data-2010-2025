"""Concurrent, content-verified cache with per-job provenance and pinned inputs."""
from collections import OrderedDict, Counter
from concurrent.futures import Future
from pathlib import Path
from threading import RLock, Lock, BoundedSemaphore, local
import json
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from . import common
from .common import BudgetExceeded, CacheLimitExceeded, atomic_json, digest, now, public_url, stable_id
from . import transport


class JobCache:
    """One collector's ownership; completed downloads can be shared safely."""
    def __init__(self, shared):
        self.shared = shared
        self.touched = set()
        self.pins = set()

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def fetch(self, *args, **kwargs):
        return self.shared.fetch(*args, **kwargs, owner=self)

    def json(self, url):
        return self.shared.json(url, owner=self)


class Cache:
    def __init__(self, root, *, timeout=90, max_bytes=5_000_000_000,
                 storage='archive', max_cache_bytes=5_000_000_000, per_host=2, memo_items=8):
        if storage not in ('compact', 'cache', 'archive') or max_cache_bytes <= 0 or per_host < 1:
            raise ValueError('Invalid storage policy, cache size or host concurrency')
        self.root = Path(root)
        self.timeout, self.max_bytes = timeout, max_bytes
        self.storage, self.max_cache_bytes = storage, max_cache_bytes
        self.per_host, self.memo_items = per_host, memo_items
        self.downloaded = self.evicted_bytes = 0
        self.assets, self.entries = {}, {}
        self.touched, self.pins = set(), set()
        self.memoized = OrderedDict()
        self._state = RLock()
        self._request_locks = [Lock() for _ in range(128)]
        self._memo_flights, self._disk_reserved, self._network_reserved = {}, {}, {}
        self._verified, self._metadata, self._hosts, self._cooldown = {}, {}, {}, {}
        self._local, self._sessions = local(), []
        self.stats = Counter()
        for meta_path in self.root.glob('http_*.*.json'):
            meta = json.loads(meta_path.read_text())
            if 'request_id' not in meta or 'sha256' not in meta:
                continue
            path = meta_path.with_suffix('')
            if path.exists() and self.retention(meta['url'], path.suffix) == 'optional':
                self.entries[path] = dict(bytes=path.stat().st_size, last_use=path.stat().st_mtime,
                    evictable=bool(meta.get('extraction_committed_at')), pins=0)

    def fork(self):
        return JobCache(self)

    def memo(self, key, build):
        """Build shared immutable inputs once, with bounded completed-object retention."""
        with self._state:
            if key in self.memoized:
                self.memoized.move_to_end(key)
                self.stats['memo_hits'] += 1
                return self.memoized[key]
            future = self._memo_flights.get(key)
            leader = future is None
            if leader:
                future = self._memo_flights[key] = Future()
        if not leader:
            return future.result()
        try:
            value = build()
            with self._state:
                self.memoized[key] = value
                self.stats['memo_builds'] += 1
                # Small text parses cannot evict a costly day/state spatial index.
                group = key[0]
                limit = 256 if group == 'vtec' else self.memo_items
                matching = [k for k in self.memoized if k[0] == group]
                for old in matching[:-limit]:
                    del self.memoized[old]
            future.set_result(value)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._state:
                self._memo_flights.pop(key, None)

    def retention(self, url, suffix):
        from .storage import supporting_file
        return 'required' if self.storage == 'archive' or supporting_file(url, suffix) else 'optional'

    def snapshot(self):
        with self._state:
            return dict(downloaded_bytes=self.downloaded,
                        cache_bytes=sum(e['bytes'] for e in self.entries.values()),
                        transport_stats=dict(self.stats))

    def _room(self, incoming=0, replacing=None):
        if self.storage == 'archive':
            return
        used = sum(e['bytes'] for e in self.entries.values())
        used += sum(n for p, n in self._disk_reserved.items() if p != replacing)
        if used + incoming <= self.max_cache_bytes:
            return
        for path, entry in sorted(self.entries.items(), key=lambda x: x[1]['last_use']):
            if used + incoming <= self.max_cache_bytes:
                break
            if not entry['evictable'] or entry['pins']:
                continue
            path.unlink(missing_ok=True)
            used -= entry['bytes']
            self.evicted_bytes += entry['bytes']
            del self.entries[path]
            self._verified.pop(path, None)
            self._metadata.pop(path, None)
        if used + incoming > self.max_cache_bytes:
            raise CacheLimitExceeded('Active/uncommitted artifacts exceed --cache-gb; increase cache or reduce workers')

    def make_room(self, incoming=0):
        with self._state:
            self._room(incoming)

    def _touch(self, path, meta, owner):
        self.assets[meta['asset_id']] = meta
        owner.touched.add(meta['asset_id'])
        if path in self.entries:
            entry = self.entries[path]
            entry['last_use'] = time.time()
            if path not in owner.pins:
                owner.pins.add(path)
                entry['pins'] += 1

    def commit(self, assets, *, owner=None):
        """A durable outcome permits eviction only after every active owner releases."""
        owner = self if owner is None else owner
        with self._state:
            for asset in assets:
                path = self.root / Path(asset['path']).name
                entry = self.entries.get(path)
                if entry is None:
                    continue
                meta_path = path.with_suffix(path.suffix + '.json')
                meta = self._metadata.get(path) or json.loads(meta_path.read_text())
                if meta['sha256'] != asset['sha256']:
                    raise ValueError('Source changed before extraction checkpoint')
                if not meta.get('extraction_committed_at'):
                    meta['extraction_committed_at'] = now()
                    atomic_json(meta_path, meta)
                entry['evictable'] = True
            for path in owner.pins:
                if path in self.entries:
                    self.entries[path]['pins'] -= 1
            owner.pins.clear()
            self._room()

    def finish(self):
        with self._state:
            if self.storage == 'compact':
                for path, entry in list(self.entries.items()):
                    if entry['evictable'] and not entry['pins']:
                        path.unlink(missing_ok=True)
                        self.evicted_bytes += entry['bytes']
                        del self.entries[path]
            self.close_sessions()
            self.memoized.clear()

    def close_sessions(self):
        """Call after workers drain, including an interrupted extraction."""
        with self._state:
            for client in self._sessions:
                client.close()
            self._sessions.clear()

    def _session(self):
        if not hasattr(self._local, 'session'):
            self._local.session = transport.session()
            with self._state:
                self._sessions.append(self._local.session)
        return self._local.session

    def _host(self, host):
        with self._state:
            return self._hosts.setdefault(host, BoundedSemaphore(self.per_host))

    def fetch(self, url, *, suffix='.bin', headers=None, identity_url=None, request_metadata=None, owner=None):
        owner = self if owner is None else owner
        headers = headers or {}
        clean = public_url(identity_url or url)
        request_id = stable_id('http', [clean, headers.get('Range')])
        path = self.root / (request_id.replace(':', '_') + suffix)
        # Same request in several jobs has one writer and one transport.
        lock = self._request_locks[int(request_id.split(':')[1][:8], 16) % len(self._request_locks)]
        with lock:
            meta_path = path.with_suffix(path.suffix + '.json')
            with self._state:
                if path.exists() and meta_path.exists():
                    meta = self._metadata.get(path) or json.loads(meta_path.read_text())
                    stamp = (path.stat().st_size, path.stat().st_mtime_ns, meta['sha256'])
                    if stamp[0] != meta['bytes'] or (self._verified.get(path) != stamp and digest(path) != meta['sha256']):
                        raise ValueError(f'Corrupt cache: {path}; remove this cache entry before retrying')
                    self._verified[path] = stamp
                    self._metadata[path] = meta
                    meta['retention'] = self.retention(clean, suffix)
                    self._touch(path, meta, owner)
                    self.stats['cache_hits'] += 1
                    return path, meta['asset_id']
            self.root.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + '.part')
            remote = url() if callable(url) else url
            host = urlsplit(remote).netloc
            for attempt in range(3):
                try:
                    with self._host(host):
                        with self._state:
                            delay = max(0, self._cooldown.get(host, 0) - time.monotonic())
                        if delay:
                            time.sleep(delay)
                        with self._state:
                            self.stats['http_requests'] += 1
                        with common.urlopen(Request(remote, headers={'User-Agent': 'us-tornado-data/optional-enrichment',
                                                'Accept-Encoding': 'identity', **headers}),
                                            timeout=self.timeout, session=self._session()) as response, temp.open('wb') as out:
                            if 'Range' in headers and response.status != 206:
                                raise ValueError('Server ignored byte range; refusing a full model download')
                            if 'Range' in headers and response.headers.get('Content-Range', '').split('/')[0] != headers['Range'].replace('bytes=', 'bytes '):
                                raise ValueError('Server returned a different byte range')
                            expected = response.headers.get('Content-Length')
                            expected = int(expected) if expected is not None else None
                            optional = self.retention(clean, suffix) == 'optional'
                            with self._state:
                                if expected is not None:
                                    if self.downloaded + sum(self._network_reserved.values()) + expected > self.max_bytes:
                                        raise BudgetExceeded('Download budget reached; raise --max-download-gb to resume')
                                    self._network_reserved[path] = expected
                                if optional:
                                    self._room(expected or 0, replacing=path)
                                    self._disk_reserved[path] = expected or 0
                            received = 0
                            while True:
                                with self._state:
                                    if expected is not None:
                                        capacity = self._network_reserved[path]
                                        if capacity == 0:
                                            break
                                    else:
                                        capacity = min(1024 * 1024, self.max_bytes - self.downloaded - sum(self._network_reserved.values()))
                                        if capacity <= 0:
                                            raise BudgetExceeded('Download budget reached during streaming response')
                                        self._network_reserved[path] = capacity
                                block = response.read(min(1024 * 1024, capacity))
                                with self._state:
                                    self.downloaded += len(block)
                                    self._network_reserved[path] -= len(block)
                                    if expected is None:
                                        self._network_reserved.pop(path, None)
                                    received += len(block)
                                    if optional:
                                        self._room(max(expected or 0, received), replacing=path)
                                        self._disk_reserved[path] = max(expected or 0, received)
                                if not block:
                                    break
                                out.write(block)
                            out.flush()
                            if expected is not None and received != expected:
                                raise ValueError('Truncated HTTP response')
                            with temp.open('rb') as check:
                                magic = check.read(4)
                            magic_by_suffix = {'.zip': (b'PK\x03\x04', b'PK\x05\x06'), '.tif': (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'),
                                               '.grib': (b'GRIB',), '.grib2': (b'GRIB',)}
                            if suffix in magic_by_suffix and magic not in magic_by_suffix[suffix]:
                                raise ValueError(f'Expected {suffix} binary payload from {clean}')
                            meta = dict(request_id=request_id, url=clean, final_url=clean if identity_url else public_url(response.url),
                                retrieved_at=now(), bytes=received, sha256=digest(temp), etag=response.headers.get('ETag'),
                                last_modified=response.headers.get('Last-Modified'), content_range=response.headers.get('Content-Range'),
                                request_range=headers.get('Range'))
                            if request_metadata is not None:
                                meta['request_metadata'] = request_metadata
                        meta.update(asset_id=stable_id('asset', [request_id, meta['sha256']]), path=str(path.resolve()),
                                    retention=self.retention(clean, suffix))
                        with self._state:
                            temp.replace(path)
                            atomic_json(meta_path, meta)
                            self._disk_reserved.pop(path, None)
                            self._network_reserved.pop(path, None)
                            if optional:
                                self.entries[path] = dict(bytes=received, last_use=time.time(), evictable=False, pins=0)
                            self._metadata[path] = meta
                            self._verified[path] = (received, path.stat().st_mtime_ns, meta['sha256'])
                            self._touch(path, meta, owner)
                        return path, meta['asset_id']
                except HTTPError as exc:
                    if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise RuntimeError(f'HTTP {exc.code} for {clean}') from None
                    retry = exc.headers.get('Retry-After') if exc.headers else None
                    try:
                        delay = float(retry)
                    except (ValueError, TypeError):
                        try:
                            delay = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (ValueError, TypeError, AttributeError):
                            delay = 2 ** attempt
                    with self._state:
                        self.stats['retries'] += 1
                        self._cooldown[host] = max(self._cooldown.get(host, 0), time.monotonic() + max(0, delay))
                except (BudgetExceeded, CacheLimitExceeded, ValueError):
                    raise
                except Exception as exc:
                    raise RuntimeError(f'{type(exc).__name__} for {clean}') from None
                finally:
                    temp.unlink(missing_ok=True)
                    with self._state:
                        self._disk_reserved.pop(path, None)
                        self._network_reserved.pop(path, None)

    def json(self, url, *, owner=None):
        owner = self if owner is None else owner
        path, asset = self.fetch(url, suffix='.json', owner=owner)
        try:
            return self.memo(('json', asset), lambda: json.loads(path.read_text())), asset
        except ValueError:
            with self._state:
                path.unlink(missing_ok=True)
                path.with_suffix('.json.json').unlink(missing_ok=True)
                self.assets.pop(asset, None)
                owner.touched.discard(asset)
                owner.pins.discard(path)
                self.entries.pop(path, None)
                self._metadata.pop(path, None)
                self._verified.pop(path, None)
            raise ValueError(f'Expected JSON from {public_url(url)}') from None
