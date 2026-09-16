"""Content-verified HTTP cache, stable keys, and shared spatial conventions."""
from dataclasses import asdict, dataclass
from functools import lru_cache
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .transport import urlopen

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



@lru_cache(maxsize=256)
def _transformer(lon, lat, inverse=False):
    local = CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m')
    transformer = Transformer.from_crs(local if inverse else 'EPSG:4326',
                                       'EPSG:4326' if inverse else local, always_xy=True)
    return transformer


def project(geometry, lon, lat, inverse=False):
    return transform(_transformer(lon, lat, inverse).transform, geometry)


def numeric(value):
    try:
        value = float(value)
        return value if pd.notna(value) and value not in (-999, -9999, -99999) else None
    except (ValueError, TypeError):
        return None


def utc(value):
    return pd.to_datetime(value, utc=True, errors='coerce')


# Imported after shared primitives to avoid a circular dependency.
from .cache import Cache
