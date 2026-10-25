"""Fixture tests for the scheduled macro updater. No network: every fetch is injected."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import macro_update as mu

BLS_CPI = json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': 'CUUR0000SA0', 'data': [
    {'year': str(y), 'period': 'M%02d' % m, 'value': '%.3f' % (100 + (y - 2023) * 12 + m)}
    for y in (2024, 2025) for m in range(1, 13)]}]}})
BLS_UNEMP = json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': 'LNS14000000', 'data': [
    {'year': '2025', 'period': 'M%02d' % m, 'value': '4.%d' % m} for m in range(1, 13)]}]}})
SING_CPI = json.dumps({'StatusCode': 200, 'Data': {'datasource': 'Singapore DOS', 'row': [
    {'rowText': 'All Items', 'columns': [{'key': '2025 %s' % mon, 'value': '2.%d' % i} for i, mon in enumerate(['Jan','Feb','Mar'])]}]}})
SING_GDP = json.dumps({'StatusCode': 200, 'Data': {'datasource': 'Singapore DOS', 'row': [
    {'rowText': 'GDP In Chained (2015) Dollars', 'columns': [{'key': '2025 1Q', 'value': '3.9'}, {'key': '2025 2Q', 'value': '4.4'}]},
    {'rowText': 'Manufacturing', 'columns': [{'key': '2025 2Q', 'value': '5.0'}]}]}})
TREASURY_CSV = 'Date,2 Yr,10 Yr\n10/23/2026,4.69,5.00\n10/24/2026,4.70,5.01\n'

def opener_ok(url):
    if 'CUUR0000SA0' in url: return BLS_CPI
    if 'LNS14000000' in url: return BLS_UNEMP
    if 'M213781' in url: return SING_CPI
    if 'M015911' in url: return SING_GDP
    if 'daily-treasury-rates' in url: return TREASURY_CSV
    raise AssertionError('unexpected url ' + url)

def opener_bls_down(url):
    if 'api.bls.gov' in url: raise ConnectionError('BLS busy')
    return opener_ok(url)

def opener_singstat_bad(url):
    if 'M015911' in url: return 'not json {'
    return opener_ok(url)

class RefreshTests(unittest.TestCase):
    def run_update(self, opener, path, prior=None, today='2026-10-25'):
        if prior is not None:
            json.dump(prior, open(path, 'w'))
        return mu.update_file(path, opener=opener, today=today)

    def test_all_success_writes_six_series_with_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = self.run_update(opener_ok, p)
            self.assertEqual(sorted(r['updated']), sorted(mu.SERIES_META))
            self.assertEqual(r['failed'], [])
            doc = json.load(open(p))
            self.assertEqual(sorted(doc['series']), sorted(mu.SERIES_META))
            for key, s in doc['series'].items():
                self.assertEqual(s['status'], 'ok')
                self.assertEqual(s['updated'], '2026-10-25')
                self.assertTrue(s['points'], key)
                self.assertIn('http', s['source_url'])
                self.assertTrue(s['attribution'], key)
            self.assertEqual(doc['failures'], {})
            # SingStat attribution must carry the ODL notice
            self.assertIn('Singapore Open Data Licence', doc['series']['sg_cpi_inflation']['attribution'])
            # Treasury values come through dated
            self.assertIn(['2026-10-24', 5.01], doc['series']['us_treasury_10y']['points'])

    def test_bls_failure_keeps_last_good_and_records_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            self.run_update(opener_ok, p, today='2026-10-24')
            before = json.load(open(p))
            r = self.run_update(opener_bls_down, p, today='2026-10-25')
            self.assertEqual(sorted(r['failed']), ['us_cpi_inflation', 'us_unemployment'])
            self.assertEqual(sorted(r['kept_previous']), ['us_cpi_inflation', 'us_unemployment'])
            doc = json.load(open(p))
            self.assertEqual(doc['series']['us_cpi_inflation']['points'], before['series']['us_cpi_inflation']['points'])
            self.assertEqual(doc['series']['us_cpi_inflation']['updated'], '2026-10-24')  # last SUCCESS date preserved
            self.assertEqual(doc['series']['sg_real_gdp_growth']['updated'], '2026-10-25')  # healthy series refresh
            self.assertIn('us_cpi_inflation', doc['failures'])
            self.assertEqual(doc['series']['sg_real_gdp_growth']['status'], 'ok')

    def test_first_run_failure_leaves_series_absent_not_invented(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = self.run_update(opener_bls_down, p)
            doc = json.load(open(p))
            self.assertNotIn('us_cpi_inflation', doc['series'])
            self.assertNotIn('us_unemployment', doc['series'])
            self.assertIn('us_cpi_inflation', doc['failures'])
            self.assertEqual(len(doc['series']), 4)

    def test_singstat_malformed_keeps_previous(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            self.run_update(opener_ok, p)
            r = self.run_update(opener_singstat_bad, p)
            self.assertIn('sg_real_gdp_growth', r['failed'])
            doc = json.load(open(p))
            self.assertEqual(doc['series']['sg_real_gdp_growth']['points'][-1], ['2025 Q2'.replace(' Q2','-04-01'), 4.4])

    def test_no_secrets_or_personal_data_in_output(self):
        import re
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            self.run_update(opener_ok, p)
            text = open(p).read()
            self.assertIsNone(re.search(r'(?i)token|secret|password|api[_-]?key|glpat|ghp_|github_pat', text))
            self.assertIsNone(re.search(r'[0-9a-f]{32,}', text))  # no hex blobs
            self.assertNotIn('justlim', text.lower())
            self.assertNotIn('gmail', text.lower())
            doc = json.loads(text)
            self.assertEqual(sorted(doc['series']), sorted(mu.SERIES_META))  # whitelist only

    def test_cpi_yoy_math(self):
        pts = [('2024-%02d-01' % m, 100.0) for m in range(1, 13)] + [('2025-01-01', 103.0)]
        out = dict(mu._growth_yoy(pts))
        self.assertAlmostEqual(out['2025-01-01'], 3.0)

    def test_period_parser(self):
        self.assertEqual(mu._period_singstat('2025 Q2'), '2025-04-01')
        self.assertEqual(mu._period_singstat('2025 Sep'), '2025-09-01')
        self.assertIsNone(mu._period_singstat('2025'))

    def test_total_failure_first_run_writes_no_fake_series(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = self.run_update(lambda u: (_ for _ in ()).throw(ConnectionError('all down')), p)
            doc = json.load(open(p))
            self.assertEqual(doc['series'], {})
            self.assertEqual(len(doc['failures']), 6)
            self.assertEqual(r['updated'], [])


SING_GDP_UNPARSEABLE = json.dumps({'StatusCode': 200, 'Data': {'row': [
    {'rowText': 'GDP In Chained (2015) Dollars', 'columns': [{'key': 'Q3-2026', 'value': '5.4'}]}]}})

def opener_singstat_empty_gdp(url):
    if 'M015911' in url: return SING_GDP_UNPARSEABLE
    return opener_ok(url)

class SingStatKeyTests(unittest.TestCase):
    def test_live_quarterly_key_format_parses(self):
        """Regression: live M015911 uses '2026 3Q' keys; must parse, not silently empty."""
        pts = mu.fetch_singstat('M015911', 'GDP In Chained (2015) Dollars', lambda url: SING_GDP)
        self.assertEqual(pts, [('2025-01-01', 3.9), ('2025-04-01', 4.4)])

    def test_unparseable_keys_fail_honestly_not_empty_success(self):
        """Matched row with unparseable keys -> series fails/falls back, never silently empty-ok."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = mu.update_file(p, opener=opener_singstat_empty_gdp, today='2026-10-25')
            self.assertIn('sg_real_gdp_growth', r['failed'])
            doc = json.load(open(p))
            self.assertNotIn('sg_real_gdp_growth', doc['series'])  # first run: absent, not invented
            self.assertIn('sg_real_gdp_growth', doc['failures'])


FRED_CPI = 'DATE,CPIAUCNS\n' + '\n'.join('%04d-%02d-01,%.3f' % (y, m, 100 + (y - 2023) * 12 + m) for y in (2024, 2025) for m in range(1, 13)) + '\n2026-01-01,.\n'
FRED_UNRATE = 'DATE,UNRATE\n2025-08-01,4.2\n2025-09-01,.\n2025-10-01,4.4\n'

def opener_bls_down_fred_up(url):
    if 'api.bls.gov' in url: raise ConnectionError('BLS busy')
    if 'fredgraph.csv?id=CPIAUCNS' in url: return FRED_CPI
    if 'fredgraph.csv?id=UNRATE' in url: return FRED_UNRATE
    return opener_ok(url)

def opener_bls_fred_down(url):
    if 'api.bls.gov' in url or 'fredgraph.csv' in url: raise ConnectionError('source busy')
    return opener_ok(url)

class FredFallbackTests(unittest.TestCase):
    def test_bls_down_fred_fallback_succeeds_with_fred_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = mu.update_file(p, opener=opener_bls_down_fred_up, today='2026-10-25')
            self.assertIn('us_cpi_inflation', r['updated'])
            self.assertIn('us_unemployment', r['updated'])
            self.assertEqual(r['failed'], [])
            doc = json.load(open(p))
            cpi = doc['series']['us_cpi_inflation']
            self.assertEqual(cpi['retrieved_via'], 'fred')
            self.assertIn('retrieved from FRED', cpi['attribution'])
            self.assertIn('fred.stlouisfed.org/series/CPIAUCNS', cpi['attribution'])
            # YoY from the same NSA index definition: 2025-01 vs 2024-01 = 12/101
            self.assertIn(['2025-02-01', round((126 / 114 - 1) * 100, 2)], cpi['points'])  # idx 2025-02=126 vs 2024-02=114

    def test_fred_missing_values_skipped(self):
        pts = mu.fetch_fred('UNRATE', lambda url: FRED_UNRATE)
        self.assertEqual(pts, [('2025-08-01', 4.2), ('2025-10-01', 4.4)])  # '.' row dropped

    def test_bls_and_fred_down_first_run_absent_not_invented(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = mu.update_file(p, opener=opener_bls_fred_down, today='2026-10-25')
            self.assertIn('us_cpi_inflation', r['failed'])
            self.assertIn('us_unemployment', r['failed'])
            doc = json.load(open(p))
            self.assertNotIn('us_cpi_inflation', doc['series'])
            self.assertNotIn('us_unemployment', doc['series'])
            self.assertIn('us_cpi_inflation', doc['failures'])

    def test_bls_down_fred_up_then_next_day_bls_back(self):
        """Fallback does not poison the series: primary source wins again when it recovers."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            mu.update_file(p, opener=opener_bls_down_fred_up, today='2026-10-24')
            r = mu.update_file(p, opener=opener_ok, today='2026-10-25')
            doc = json.load(open(p))
            self.assertEqual(doc['series']['us_cpi_inflation']['retrieved_via'], 'bls')
            self.assertNotIn('FRED', doc['series']['us_cpi_inflation']['attribution'])


FRED_CPI_NEWHEADER = 'observation_date,CPIAUCNS\n' + '\n'.join('%04d-%02d-01,%.3f' % (y, m, 100 + (y - 2023) * 12 + m) for y in (2024, 2025) for m in range(1, 13)) + '\n'
FRED_CPI_SHORT = 'DATE,CPIAUCNS\n2025-10-01,300.1\n2025-11-01,300.5\n'  # <13 months: YoY impossible

def opener_fred_newheader(url):
    if 'api.bls.gov' in url: raise ConnectionError('BLS busy')
    if 'fredgraph.csv?id=CPIAUCNS' in url: return FRED_CPI_NEWHEADER
    if 'fredgraph.csv?id=UNRATE' in url: return FRED_UNRATE
    return opener_ok(url)

def opener_fred_short(url):
    if 'api.bls.gov' in url: raise ConnectionError('BLS busy')
    if 'fredgraph.csv?id=CPIAUCNS' in url: return FRED_CPI_SHORT
    if 'fredgraph.csv?id=UNRATE' in url: return FRED_UNRATE
    return opener_ok(url)

class FredHeaderAndEmptyOutputTests(unittest.TestCase):
    def test_observation_date_header_variant_parses(self):
        """Real FRED csv may use observation_date instead of DATE."""
        pts = mu.fetch_fred('CPIAUCNS', lambda url: FRED_CPI_NEWHEADER)
        self.assertEqual(pts[0], ('2024-01-01', 113.0))
        self.assertEqual(pts[-1], ('2025-12-01', 136.0))

    def test_newheader_fallback_updates_series(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = mu.update_file(p, opener=opener_fred_newheader, today='2026-10-25')
            self.assertIn('us_cpi_inflation', r['updated'])
            doc = json.load(open(p))
            self.assertEqual(doc['series']['us_cpi_inflation']['retrieved_via'], 'fred')

    def test_empty_yoy_output_is_failure_not_empty_success(self):
        """Too little history for YoY must fail honestly: absent, in failures, NOT in updated."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'macro_data.json')
            r = mu.update_file(p, opener=opener_fred_short, today='2026-10-25')
            self.assertNotIn('us_cpi_inflation', r['updated'])
            self.assertIn('us_cpi_inflation', r['failed'])
            doc = json.load(open(p))
            self.assertNotIn('us_cpi_inflation', doc['series'])
            self.assertIn('us_cpi_inflation', doc['failures'])
            self.assertIn('us_unemployment', r['updated'])  # other series unaffected

if __name__ == '__main__':
    unittest.main()
