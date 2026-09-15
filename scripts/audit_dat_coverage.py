"""Audit the saved NOAA snapshot; standard library only, no downloads or raw-data edits.

Run from anywhere: python3 scripts/audit_dat_coverage.py
Outputs: reports/dat_coverage/dat_coverage_metrics.json and candidate-level JSONL for review.
Matching is a sensitivity analysis of nearby start locations/times, not a verified join.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import gzip
import hashlib
import json
import math
import re

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
OUT = ROOT / 'reports' / 'dat_coverage'
YEARS = range(2010, 2026)
THRESHOLDS = {'strict': (1, 10), 'standard': (5, 30), 'loose': (10, 60)}


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def meaningful(value):
    return value is not None and str(value).strip().upper() not in ('', 'NULL', 'NONE', 'N/A', 'UNKNOWN', '-99')


def guid(value):
    return str(value).strip('{}').upper() if meaningful(value) else None


def rating(value):
    return str(value or '').strip().upper()


def tornado(value):
    return bool(re.fullmatch(r'EF(?:[0-5]\+?|U)', rating(value)))


def valid_xy(lat, lon):
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0)


def km(a, b, c, d):
    p, q = math.radians(a), math.radians(c)
    h = math.sin((q-p)/2)**2 + math.cos(p)*math.cos(q)*math.sin(math.radians(d-b)/2)**2
    return 12742 * math.asin(min(1, math.sqrt(h)))


def normalized_key(p):
    # A text/date grouping proxy, not a globally unique tornado identifier.
    name = ' '.join(str(p.get('event_id') or '').casefold().split())
    return (int(p['stormdate']) // 86400000, name) if name else None


def load_dat():
    stats, lines, points, polygons, provenance = {}, [], [], [], []
    for year in YEARS:
        for layer in ('lines', 'points', 'polygons'):
            folder = DATA / 'nws_dat' / str(year) / layer
            index_path = folder / 'index.json'
            index = json.loads(index_path.read_text())
            provenance.append({'path': str(index_path.relative_to(ROOT)), 'sha256': digest(index_path)})
            ratings, quality, offices, gids, event_keys = Counter(), Counter(), Counter(), Counter(), set()
            s = Counter()
            for batch in index['batches']:
                path = folder / batch['file']
                if digest(path) != batch['sha256']:
                    raise ValueError(f'Changed DAT batch: {path}')
                features = json.loads(path.read_text())['features']
                if len(features) != batch['features']:
                    raise ValueError(f'Wrong feature count: {path}')
                for f in features:
                    p = f['properties']; g = f.get('geometry')
                    if datetime.fromtimestamp(p['stormdate']/1000, timezone.utc).year != year:
                        raise ValueError('DAT year mismatch')
                    s['total'] += 1
                    ratings[rating(p.get('efscale'))] += 1
                    quality[rating(p.get('qc'))] += 1
                    office = rating(p.get('wfo') or p.get('office'))
                    if office:
                        offices[office] += 1
                    if gid := guid(p.get('globalid')):
                        gids[gid] += 1
                    if key := normalized_key(p):
                        event_keys.add(key)
                    s['geometry_missing'] += not bool(g and g.get('coordinates'))
                    s['path_guid_present'] += bool(guid(p.get('path_guid')))
                    s['event_id_missing'] += not meaningful(p.get('event_id'))
                    s['rated_ef0_5'] += bool(re.fullmatch('EF[0-5]', rating(p.get('efscale'))))
                    s['tornado_labeled'] += tornado(p.get('efscale'))
                    item = {k: p.get(k) for k in ('objectid', 'globalid', 'path_guid', 'stormdate', 'event_id', 'efscale', 'qc')}
                    item.update(year=year, office=office, key=key)
                    if layer == 'lines':
                        item.update({k: p.get(k) for k in ('startlat', 'startlon', 'starttime', 'endtime', 'efnum', 'maxwind')})
                        t = p.get('starttime')
                        item['t'] = (t if isinstance(t, (int, float)) and t > 0 else p['stormdate']) / 1000
                        item['lat'], item['lon'] = p.get('startlat'), p.get('startlon')
                        s['starttime_missing'] += not (isinstance(t, (int, float)) and t > 0)
                        s['invalid_start_location'] += not valid_xy(item['lat'], item['lon'])
                        lines.append(item)
                    elif layer == 'points':
                        item.update({k: p.get(k) for k in ('damage_txt', 'dod_txt', 'damage', 'dod', 'windspeed', 'image', 'surveytype')})
                        s['damage_indicator_present'] += meaningful(p.get('damage_txt'))
                        s['dod_text_present'] += meaningful(p.get('dod_txt'))
                        s['image_reference_present'] += meaningful(p.get('image'))
                        s['surveytype_present'] += meaningful(p.get('surveytype'))
                        points.append(item)
                    else:
                        polygons.append(item)
            assert s['total'] == index['features']
            s['distinct_globalids'] = len(gids)
            s['duplicate_globalid_rows'] = sum(v-1 for v in gids.values())
            s['distinct_day_event_names'] = len(event_keys)
            stats[f'{year}_{layer}'] = dict(s, ratings=dict(ratings), qc=dict(quality), offices=dict(offices))
    return stats, lines, points, polygons, provenance


def load_reference():
    spc, ncei = [], []
    with (DATA/'spc/tornadoes_2010_2025.csv').open(newline='') as f:
        for r in csv.DictReader(f):
            assert r['tz'] == '3', 'Unexpected SPC time zone; review specification.'
            dt = datetime.fromisoformat(r['date']+'T'+r['time']).replace(tzinfo=timezone(timedelta(hours=-6)))
            spc.append(dict(id=r['om'], year=int(r['yr']), state=r['st'], ef=int(r['mag']),
                            t=dt.timestamp(), lat=float(r['slat']), lon=float(r['slon'])))
    assert len({(r['year'], r['id']) for r in spc}) == len(spc), 'SPC duplicate IDs'
    for year in YEARS:
        with (DATA/f'ncei_storm_events/tornado/{year}_details.csv').open(newline='') as f:
            for r in csv.DictReader(f):
                z = re.fullmatch(r'[A-Z]+([+-]\d+)', r['CZ_TIMEZONE'])
                if z is None:
                    raise ValueError(f'Unknown NCEI time zone {r["CZ_TIMEZONE"]}')
                local = datetime.strptime(r['BEGIN_DATE_TIME'], '%d-%b-%y %H:%M:%S')
                dt = local.replace(tzinfo=timezone(timedelta(hours=int(z[1]))))
                ncei.append(dict(id=r['EVENT_ID'], year=year, state=r['STATE'], office=r['WFO'],
                                 ef=r['TOR_F_SCALE'], t=dt.timestamp(),
                                 lat=float(r['BEGIN_LAT']) if r['BEGIN_LAT'] else None,
                                 lon=float(r['BEGIN_LON']) if r['BEGIN_LON'] else None))
    return spc, ncei


def candidates(refs, lines):
    hours = defaultdict(list)
    for i, line in enumerate(lines):
        if tornado(line['efscale']) and valid_xy(line['lat'], line['lon']):
            hours[int(line['t']//3600)].append(i)
    result = []
    for r in refs:
        found = []
        if valid_xy(r['lat'], r['lon']):
            hour = int(r['t']//3600)
            for bucket in range(hour-1, hour+2):
                for i in hours[bucket]:
                    line = lines[i]
                    minutes = abs(r['t']-line['t'])/60
                    if minutes <= 60 and abs(r['lat']-line['lat']) < .1:
                        distance = km(r['lat'], r['lon'], line['lat'], line['lon'])
                        if distance <= 10:
                            found.append(dict(line_index=i, objectid=line['objectid'], km=distance,
                                              minutes=minutes, dat_rating=line['efscale'], dat_office=line['office']))
        by_threshold = {name: [v for v in found if v['km'] <= d and v['minutes'] <= t]
                        for name, (d,t) in THRESHOLDS.items()}
        result.append(dict(r, candidates=by_threshold))
    return result


def aggregate(matches, key):
    groups = defaultdict(list)
    for r in matches:
        groups[str(key(r))].append(r)
    result = {}
    for label, records in groups.items():
        total = len(records)
        d = dict(total=total, invalid_start=sum(not valid_xy(r['lat'],r['lon']) for r in records))
        for name in THRESHOLDS:
            matched = sum(bool(r['candidates'][name]) for r in records)
            d[name] = dict(matched=matched, percent=100*matched/total,
                           multiple_candidates=sum(len(r['candidates'][name]) > 1 for r in records))
        result[label] = d
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stats, lines, points, polygons, provenance = load_dat()
    spc, ncei = load_reference()
    sm, nm = candidates(spc, lines), candidates(ncei, lines)
    for source, matches in [('spc',sm),('ncei',nm)]:
        with gzip.open(OUT/f'dat_{source}_candidates.jsonl.gz', 'wt') as f:
            for r in matches:
                f.write(json.dumps(r)+'\n')
    line_gid = defaultdict(list)
    line_key = defaultdict(list)
    for i,line in enumerate(lines):
        if g := guid(line['globalid']):line_gid[g].append(i)
        if line['key']:line_key[line['key']].append(i)
    links = {}
    for layer, features in [('points',points),('polygons',polygons)]:
        year_stats = defaultdict(Counter); per_line = Counter(); per_name = Counter()
        for p in features:
            s = year_stats[p['year']]; s['total'] += 1
            linked = line_gid.get(guid(p['path_guid']),[])
            if len(linked)==1:
                s['exact_guid_link'] += 1; per_line[linked[0]] += 1
                s['guid_link_to_tornado_line'] += tornado(lines[linked[0]]['efscale'])
                s['guid_link_cross_year'] += lines[linked[0]]['year'] != p['year']
            elif meaningful(p['path_guid']):s['unresolved_guid'] += 1
            named = line_key.get(p['key'],[])
            if p['office']:
                named = [i for i in named if lines[i]['office']==p['office']]
            if len(named)==1:s['unique_name_date_office_proxy'] += 1
            if p['key']:per_name[(p['year'], p['key'], p['office'])] += 1
        links[layer] = dict(by_year={str(k):dict(v) for k,v in year_stats.items()},
                           lines_with_exact_link=len(per_line),
                           top_groups=[dict(year=k[0], day=k[1][0], name=k[1][1], office=k[2], features=v)
                                       for k,v in per_name.most_common(10)])
    windows = {}
    for first in [2010,2013,2017,2019,2021,2023]:
        subset = [r for r in sm if r['year']>=first]
        nsubset = [r for r in nm if r['year']>=first]
        windows[str(first)] = dict(spc=aggregate(subset,lambda r:'all')['all'],
                                  by_ef=aggregate(subset,lambda r:r['ef']),
                                  by_state=aggregate(subset,lambda r:r['state']),
                                  rated=aggregate([r for r in subset if r['ef']>=0],lambda r:'all')['all'],
                                  rated_by_state=aggregate([r for r in subset if r['ef']>=0],lambda r:r['state']),
                                  ncei_by_office=aggregate(nsubset,lambda r:r['office']))
    usage = Counter(v['line_index'] for r in sm for v in r['candidates']['standard'])
    unique = [(r,r['candidates']['standard'][0]) for r in sm
              if len(r['candidates']['standard'])==1 and usage[r['candidates']['standard'][0]['line_index']]==1]
    comparable = [(r,v) for r,v in unique if r['ef']>=0 and re.fullmatch(r'EF[0-5]',v['dat_rating'])]
    exact_agree = sum(v['dat_rating']==f'EF{r["ef"]}' for r,v in comparable)
    # Diagnose suspicious timestamps without modifying them or accepting extra matches.
    days = defaultdict(list)
    for r in spc:
        days[int(r['t']//86400)].append(r)
    clock_offsets = defaultdict(Counter)
    clock_examples = []
    for line in lines:
        if not tornado(line['efscale']) or not valid_xy(line['lat'], line['lon']):
            continue
        day = int(line['stormdate']//86400000)
        nearby = []
        for day2 in range(day-1, day+2):
            for r in days[day2]:
                distance = km(line['lat'], line['lon'], r['lat'], r['lon'])
                if distance <= 5:
                    nearby.append((distance, r))
        if nearby:
            distance, r = min(nearby, key=lambda item: item[0])
            delta = (line['t']-r['t'])/3600
            clock_offsets[line['year']][round(delta)] += 1
            if line['year']==2012 and abs(delta)>1 and len(clock_examples)<5:
                clock_examples.append(dict(dat_objectid=line['objectid'], spc_id=r['id'],
                                           distance_km=distance, dat_minus_spc_hours=delta))
    metrics = dict(clock_diagnostics=dict(offset_histograms={str(k):dict(v) for k,v in clock_offsets.items()},
                                          examples_2012=clock_examples,
                                          starttime_equals_stormdate=sum(r['starttime']==r['stormdate'] for r in lines)),
                   snapshot=json.loads((DATA/'download_manifest_2010_2025.json').read_text())['completed_at'],
                   thresholds_km_minutes=THRESHOLDS, dat=stats, links=links,
                   spc_yearly=aggregate(sm,lambda r:r['year']),
                   spc_rated_yearly=aggregate([r for r in sm if r['ef']>=0],lambda r:r['year']), ncei_yearly=aggregate(nm,lambda r:r['year']),
                   windows=windows, unique_standard_pairs=len(unique),
                   comparable_unique_pairs=len(comparable), rating_agreement=exact_agree,
                   dat_lines_without_standard_spc_candidate=len(lines)-len(usage),
                   input_indexes=provenance,
                   reference_hashes={str(p.relative_to(ROOT)):digest(p) for p in
                       [DATA/'spc/tornadoes_2010_2025.csv',*sorted((DATA/'ncei_storm_events/tornado').glob('*_details.csv'))]})
    (OUT/'dat_coverage_metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    print('Year SPC DAT-tornado-lines strict% standard% loose% points polygons')
    for year in YEARS:
        m=metrics['spc_yearly'][str(year)]
        print(year,m['total'],stats[f'{year}_lines']['tornado_labeled'],
              *[round(m[t]['percent'],1) for t in THRESHOLDS],stats[f'{year}_points']['total'],stats[f'{year}_polygons']['total'])
    print('Window summaries')
    for first,w in windows.items():print(first,w['spc'])
    print('Unique pairs, rated, agreeing:',len(unique),len(comparable),exact_agree)
    print('Saved metrics and candidate-level files under reports/dat_coverage/.')


if __name__=='__main__':main()
