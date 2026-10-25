"""Scheduled public macro data updater (repo-side job).

Refreshes macro_data.json from official free public sources. Public macro series only -
never keys, holdings, or personal data. Per-series independence: a failed series keeps its
last good dated values and is listed in failures; nothing is invented or substituted.

Sources (all free, key-free, official):
  US BLS public API: CPI inflation (CUUR0000SA0), unemployment (LNS14000000) - public domain
  FRED fredgraph.csv fallback (key-free) for the two US series: CPIAUCNS, UNRATE (same BLS series via St. Louis Fed, citation required)
  SingStat Table Builder API: SG CPI (M213781), SG real GDP growth (M015911) - Singapore ODL, attribution required
  US Treasury daily yield curve CSV: 2-year and 10-year par yields - US government public data
"""
import csv, io, json, time, urllib.request
from datetime import date

UA = {'User-Agent': 'macro-data-updater/1.0 (public economic series)', 'Accept': 'application/json'}
OUT = 'macro_data.json'

SERIES_META = {
    'us_cpi_inflation':  {'name': 'US CPI inflation', 'unit': '% YoY (not seasonally adjusted)',
                          'source': 'US Bureau of Labor Statistics', 'source_url': 'https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0',
                          'attribution': 'Source: US Bureau of Labor Statistics (public domain), retrieved via the BLS public API.',
                          'attribution_fred': 'Source: U.S. Bureau of Labor Statistics, Consumer Price Index for All Urban Consumers: All Items in U.S. City Average, Not Seasonally Adjusted (CPIAUCNS), retrieved from FRED, Federal Reserve Bank of St. Louis (https://fred.stlouisfed.org/series/CPIAUCNS), accessed {accessed}.'},
    'us_unemployment':   {'name': 'US unemployment', 'unit': '% seasonally adjusted',
                          'source': 'US Bureau of Labor Statistics', 'source_url': 'https://api.bls.gov/publicAPI/v1/timeseries/data/LNS14000000',
                          'attribution': 'Source: US Bureau of Labor Statistics (public domain), retrieved via the BLS public API.',
                          'attribution_fred': 'Source: U.S. Bureau of Labor Statistics, Unemployment Rate (UNRATE), retrieved from FRED, Federal Reserve Bank of St. Louis (https://fred.stlouisfed.org/series/UNRATE), accessed {accessed}.'},
    'sg_cpi_inflation':  {'name': 'SG CPI inflation', 'unit': '% YoY',
                          'source': 'Singapore Department of Statistics (SingStat)', 'source_url': 'https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213781',
                          'attribution': 'Contains information from SingStat Table Builder (Singapore Department of Statistics) accessed on {accessed} which is made available under the terms of the Singapore Open Data Licence version 1.0 (https://data.gov.sg/open-data-licence).'},
    'sg_real_gdp_growth': {'name': 'SG real GDP growth', 'unit': '% YoY',
                          'source': 'Singapore Department of Statistics (SingStat)', 'source_url': 'https://tablebuilder.singstat.gov.sg/api/table/tabledata/M015911',
                          'attribution': 'Contains information from SingStat Table Builder (Singapore Department of Statistics) accessed on {accessed} which is made available under the terms of the Singapore Open Data Licence version 1.0 (https://data.gov.sg/open-data-licence).'},
    'us_treasury_2y':    {'name': 'US Treasury 2-year yield', 'unit': '% (daily par yield)',
                          'source': 'US Department of the Treasury', 'source_url': 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv',
                          'attribution': 'Source: US Department of the Treasury, Daily Treasury Par Yield Curve Rates (US government public data).'},
    'us_treasury_10y':   {'name': 'US Treasury 10-year yield', 'unit': '% (daily par yield)',
                          'source': 'US Department of the Treasury', 'source_url': 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv',
                          'attribution': 'Source: US Department of the Treasury, Daily Treasury Par Yield Curve Rates (US government public data).'},
}

def _get(url, accept='application/json', timeout=12, opener=None):
    """One HTTP GET -> text. `opener` injectable for tests."""
    if opener is not None:
        return opener(url)
    req = urllib.request.Request(url, headers={'User-Agent': UA['User-Agent'], 'Accept': accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace')

def _growth_yoy(pts, lag=12):
    """[(date, value)] monthly -> [(date, %YoY)] matching periods only."""
    by_date = dict(pts)
    out = []
    for d, v in pts:
        y, m, _ = d.split('-')
        prev = by_date.get('%04d-%s-01' % (int(y) - 1, m))
        if prev not in (None, 0):
            out.append((d, round((v / prev - 1) * 100, 2)))
    return out

MONTHS = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
          'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}

def _period_singstat(key):
    """SingStat column keys like '2025 Q3' or '2025 Sep' -> period start date, or None."""
    import re
    key = key.strip()
    m = re.fullmatch(r'(\d{4}) Q([1-4])', key) or re.fullmatch(r'(\d{4}) ([1-4])Q', key)
    if m:
        return '%s-%02d-01' % (m.group(1), (int(m.group(2)) - 1) * 3 + 1)
    m = re.fullmatch(r'(\d{4}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', key)
    if m:
        return m.group(1) + '-' + MONTHS[m.group(2)] + '-01'
    return None

def fetch_bls(series_id, opener=None):
    """-> [(date, value)] ascending, monthly."""
    text = _get('https://api.bls.gov/publicAPI/v1/timeseries/data/' + series_id, opener=opener)
    j = json.loads(text)
    if j.get('status') != 'REQUEST_SUCCEEDED':
        raise ValueError('BLS request not successful')
    pts = []
    for ser in j['Results']['series']:
        for x in ser['data']:
            if x['period'].startswith('M') and 1 <= int(x['period'][1:]) <= 12:
                try:
                    v = float(x['value'])
                except (TypeError, ValueError):
                    continue
                pts.append((date(int(x['year']), int(x['period'][1:]), 1).isoformat(), v))
    if not pts:
        raise ValueError('BLS response carried no usable points')
    return sorted(pts)


def fetch_fred(series_id, opener=None):
    """Key-free public FRED CSV -> [(date, value)] ascending. '.' marks missing values."""
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=' + series_id
    text = _get(url, accept='text/csv', opener=opener)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError('FRED CSV empty')
    keys = list(rows[0].keys())
    date_col = next((c for c in keys if c.strip().lower() in ('date', 'observation_date')), None)
    val_col = next((c for c in keys if c.strip().lower() not in ('date', 'observation_date')), None)
    if not date_col or not val_col:
        raise ValueError('FRED CSV columns missing (expected a date column and a value column)')
    pts = []
    for r in rows:
        v = (r.get(val_col) or '').strip()
        d = (r.get(date_col) or '').strip()
        if v in ('', '.') or not d:
            continue
        try:
            pts.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    if not pts:
        raise ValueError('FRED response carried no usable points')
    return sorted(pts)

def fetch_singstat(table_id, row_match, opener=None):
    """-> [(date, value)] ascending for the named row."""
    url = 'https://tablebuilder.singstat.gov.sg/api/table/tabledata/' + table_id
    j = json.loads(_get(url, opener=opener))
    if j.get('StatusCode') != 200:
        raise ValueError('SingStat request not successful')
    data = j['Data']
    row = next(x for x in data['row'] if x['rowText'].strip().lower() == row_match.lower())
    pts = []
    for x in row['columns']:
        dt = _period_singstat(x['key'])
        try:
            v = float(x['value'])
        except Exception:
            continue
        if dt:
            pts.append((dt, v))
    if not pts:
        raise ValueError('SingStat row matched but no dated points parsed')
    return sorted(pts)

def fetch_treasury(year, opener=None):
    """-> {'us_treasury_2y': [(date, pct)], 'us_treasury_10y': [...]} from the official daily CSV."""
    url = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
           'daily-treasury-rates.csv/%s/all?type=daily_treasury_yield_curve&field_tdr_date_value=%s&page&_format=csv' % (year, year))
    text = _get(url, accept='text/csv', opener=opener)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError('Treasury CSV empty')
    cols = rows[0].keys()
    def pick(*names):
        for n in names:
            for c in cols:
                if c.strip().lower().startswith(n):
                    return c
        raise ValueError('Treasury column missing: ' + '/'.join(names))
    c_date, c2, c10 = pick('date'), pick('2 yr', '2 year'), pick('10 yr', '10 year')
    out = {'us_treasury_2y': [], 'us_treasury_10y': []}
    for r in rows:
        d = r[c_date].strip()
        for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
            try:
                from datetime import datetime
                d_iso = datetime.strptime(d, fmt).date().isoformat()
                break
            except ValueError:
                d_iso = None
        if not d_iso:
            continue
        for col, key in ((c2, 'us_treasury_2y'), (c10, 'us_treasury_10y')):
            try:
                out[key].append((d_iso, float(r[col])))
            except (TypeError, ValueError):
                continue
    if not out['us_treasury_2y'] or not out['us_treasury_10y']:
        raise ValueError('Treasury yields parsed empty')
    return {k: sorted(v) for k, v in out.items()}

def build_points(opener=None):
    """Fetch every series independently. -> ({key: points}, {key: error})."""
    points, errors, via = {}, {}, {}
    cpi_pts, cpi_via = [], None
    try:
        cpi_pts, cpi_via = _growth_yoy(fetch_bls('CUUR0000SA0', opener))[-24:], 'bls'
    except Exception:
        try:
            cpi_pts, cpi_via = _growth_yoy(fetch_fred('CPIAUCNS', opener))[-24:], 'fred'
        except Exception:
            pass
    if cpi_pts:
        points['us_cpi_inflation'] = cpi_pts
        via['us_cpi_inflation'] = cpi_via
    else:
        errors['us_cpi_inflation'] = 'US BLS and FRED fallback both yielded no usable CPI values; kept previous dated values.'
    un_pts, un_via = [], None
    try:
        un_pts, un_via = fetch_bls('LNS14000000', opener)[-24:], 'bls'
    except Exception:
        try:
            un_pts, un_via = fetch_fred('UNRATE', opener)[-24:], 'fred'
        except Exception:
            pass
    if un_pts:
        points['us_unemployment'] = un_pts
        via['us_unemployment'] = un_via
    else:
        errors['us_unemployment'] = 'US BLS and FRED fallback both yielded no usable unemployment values; kept previous dated values.'
    try:
        points['sg_cpi_inflation'] = fetch_singstat('M213781', 'All Items', opener)[-24:]
    except Exception as e:
        errors['sg_cpi_inflation'] = 'SingStat unavailable; kept previous dated values.'
    try:
        points['sg_real_gdp_growth'] = fetch_singstat('M015911', 'GDP In Chained (2015) Dollars', opener)[-24:]
    except Exception as e:
        errors['sg_real_gdp_growth'] = 'SingStat unavailable; kept previous dated values.'
    try:
        y = str(date.today().year)
        try:
            t = fetch_treasury(y, opener)
        except Exception:
            t = fetch_treasury(str(int(y) - 1), opener)  # year boundary: fall back to last year's file
        points['us_treasury_2y'] = t['us_treasury_2y'][-60:]
        points['us_treasury_10y'] = t['us_treasury_10y'][-60:]
    except Exception as e:
        errors['us_treasury_2y'] = 'US Treasury file unavailable; kept previous dated values.'
        errors['us_treasury_10y'] = 'US Treasury file unavailable; kept previous dated values.'
    return points, errors, via

def update_file(path=OUT, opener=None, today=None):
    """Merge fresh points into the existing data file. Last good values survive any failure."""
    today = today or date.today().isoformat()
    try:
        old = json.load(open(path))
    except Exception:
        old = {'series': {}, 'failures': {}, 'generated': None}
    points, errors, via = build_points(opener)
    series = {}
    for key, meta in SERIES_META.items():
        prev = old.get('series', {}).get(key)
        if key in points and points[key]:
            attr = meta.get('attribution_fred') if via.get(key) == 'fred' else meta['attribution']
            entry = {k: v for k, v in meta.items() if k != 'attribution_fred'}
            series[key] = {**entry, 'attribution': attr.format(accessed=today),
                           'retrieved_via': via.get(key, 'primary'),
                           'points': points[key], 'updated': today, 'status': 'ok'}
        elif prev:
            series[key] = prev  # keep last good dated values; failure recorded below
        # else: never present and failed now -> absent, listed in failures (unavailable beats inferred)
    failures = {**{k: v for k, v in old.get('failures', {}).items() if k not in points and k in errors}, **errors}
    doc = {'generated': today, 'series': series, 'failures': failures,
           'note': 'Public macro series only. Each series carries its own source, dates and last-successful update date. Failed refreshes keep previous dated values and are listed in failures; no values are estimated.'}
    json.dump(doc, open(path, 'w'), indent=1)
    return {'updated': sorted(points), 'failed': sorted(errors), 'kept_previous': sorted(k for k in errors if k in (old.get('series') or {}))}

if __name__ == '__main__':
    import sys
    result = update_file(sys.argv[1] if len(sys.argv) > 1 else OUT)
    print(json.dumps(result, indent=1))
    if not result['updated'] and result['failed']:
        sys.exit(1)  # everything failed AND nothing refreshed: visible failure, file untouched where possible
