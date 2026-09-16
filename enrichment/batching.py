"""Deterministic acquisition plans; event windows and native pixels stay unchanged."""
from collections import Counter, defaultdict
import math

import pandas as pd
from shapely.geometry import Point

from .common import project, utc


def radar_window(event, config):
    start = utc(event['start_utc'])
    center = Point(event['longitude'], event['latitude'])
    region = project(project(center, center.x, center.y).buffer(config.radar_radius_km * 1000),
                     center.x, center.y, inverse=True)
    return (start - pd.Timedelta(minutes=config.radar_before_minutes),
            start + pd.Timedelta(minutes=config.radar_after_minutes),
            tuple(round(v, 6) for v in region.bounds))


def union_bounds(bounds):
    return (min(b[0] for b in bounds), min(b[1] for b in bounds),
            max(b[2] for b in bounds), max(b[3] for b in bounds))


def radar_plans(events, config):
    """Share nearby six-hour groups, with adaptive response splitting at fetch time."""
    groups = defaultdict(list)
    for event in events:
        if not event['usable'] or pd.isna(utc(event['start_utc'])):
            continue
        key = (utc(event['start_utc']).floor('6h').isoformat(),
               math.floor(event['longitude']), math.floor(event['latitude']))
        groups[key].append((event['tornado_id'], radar_window(event, config)))
    plans = {}
    for members in groups.values():
        window = (min(w[0] for _, w in members), max(w[1] for _, w in members),
                  union_bounds([w[2] for _, w in members]))
        plans.update((key, window) for key, _ in members)
    return plans


def warning_window(start):
    return start.floor('D') - pd.Timedelta(days=1), start.floor('D') + pd.Timedelta(days=1)


def warning_partition(start):
    # One-day overlap includes the previous day for events at a month boundary.
    month = start.normalize().replace(day=1)
    return month - pd.Timedelta(days=1), month + pd.offsets.MonthBegin(1)


def warning_plans(events):
    """Bulk only months with at least three distinct selected onset days."""
    months = defaultdict(set)
    valid = [e for e in events if e['usable'] and pd.notna(utc(e['start_utc']))]
    counts = Counter(utc(e['start_utc']).floor('D') for e in valid)
    for day in counts:
        months[day.strftime('%Y-%m')].add(day)
    plans, dates, text_days = {}, set(), set()
    for event in valid:
        start = utc(event['start_utc'])
        day = start.floor('D')
        plans[event['tornado_id']] = (warning_partition(start) if len(months[start.strftime('%Y-%m')]) >= 3
                                     else warning_window(start))
        for stamp in (day - pd.Timedelta(days=1), day):
            dates.add(stamp.strftime('%Y%m%d'))
            if counts[day] >= 3:
                text_days.add(stamp.strftime('%Y%m%d'))
    return plans, dates, text_days


def nlcd_plans(events, config):
    """Merge neighboring requests only when their union has bounded pixel overhead.

    100 km bins bound planning work; a group may span a bin edge. No raster is
    resampled and a large standalone footprint is never clipped to a bin.
    """
    from .land import nlcd_bounds
    groups = defaultdict(list)
    for event in events:
        if not event['usable'] or not event.get('area_wkt'):
            continue
        if not (-125 <= event['longitude'] <= -66 and 24 <= event['latitude'] <= 50):
            continue
        bounds = nlcd_bounds(event['area_wkt'])
        key = (event['year'] - config.nlcd_year_lag,
               math.floor((bounds[0] + bounds[2]) / 200_000),
               math.floor((bounds[1] + bounds[3]) / 200_000))
        groups[key].append((event['tornado_id'], bounds))
    plans = {}
    for members in groups.values():
        clusters = []
        for key, bounds in sorted(members, key=lambda item: (*item[1], item[0])):
            area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
            candidates = []
            for i, (ids, old, total) in enumerate(clusters):
                merged = union_bounds([bounds, old])
                size = (merged[2] - merged[0]) * (merged[3] - merged[1])
                if size <= 2 * (total + area) and size <= 900 * 4_000_000:
                    candidates.append((size, i, merged))
            if candidates:
                _, i, merged = min(candidates)
                ids, _, total = clusters[i]
                clusters[i] = (ids + [key], merged, total + area)
            else:
                clusters.append(([key], bounds, area))
        for ids, bounds, _ in clusters:
            plans.update((key, bounds) for key in ids)
    return plans
