"""Synthetic checks for reversible station preparation, missingness and chronology."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import csv
import hashlib
import json
import tempfile
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/p) for p in ('scripts', 'src')]
from insitu.preparation import prepare_series, run


class PreparationTests(unittest.TestCase):
    def setUp(self):
        """Use an exact UTC window without provider data or downloads."""
        self.start = datetime(2026, 6, 17, tzinfo=timezone.utc)

    def records(self, values):
        """Create hourly observations with explicit raw values and retained flags."""
        return [(self.start+timedelta(hours=i), v, {'qcFlag':'S'}) for i,v in enumerate(values)]

    def test_threshold_elapsed_time_and_whole_run(self):
        """Twelve hourly observations span eleven hours; thirteen span twelve and all mask."""
        for n in (12, 13):
            rows, events, periods, changes = prepare_series(self.records([2.]*n), self.start,
                                                           self.start+timedelta(hours=n-1), .01)
            self.assertEqual(len(periods), int(n==13))
            self.assertEqual(sum(math.isnan(r['soil_moisture_m3_m3']) for r in rows), n if n==13 else 0)
            if periods:
                self.assertEqual(periods[0]['elapsed_hours'], 12)
                self.assertEqual(periods[0]['mask_status'], 'masked_by_policy')
                self.assertEqual(len(events), n)
                self.assertEqual(rows[0]['raw_value'], 2.)

    def test_missing_and_gaps_break_runs(self):
        """A missing scheduled label or supplied null breaks equal values on either side."""
        for omitted in (True, False):
            records = self.records([4.]*15)
            if omitted:
                del records[7]
            else:
                records[7] = (records[7][0], None, {})
            rows, events, periods, _ = prepare_series(records, self.start, self.start+timedelta(hours=14), .01)
            self.assertEqual(periods, [])
            self.assertTrue(math.isnan(rows[7]['soil_moisture_m3_m3']))
            self.assertEqual(len(events), 1)

    def test_first_duplicate_even_when_missing(self):
        """Source order decides duplicates before sorting, including a missing first entry."""
        records = [(self.start+timedelta(hours=1), 20., {}), (self.start, None, {'original':True}),
                   (self.start, 50., {'replacement':True})]
        rows, events, _, _ = prepare_series(records, self.start, self.start+timedelta(hours=1), .01)
        self.assertTrue(math.isnan(rows[0]['soil_moisture_m3_m3']))
        self.assertEqual(rows[0]['provider_flags'], {'original':True})
        duplicate = next(e for e in events if e['action']=='duplicate_discarded')
        self.assertEqual(duplicate['retained_source_index'], 1)
        self.assertEqual(duplicate['discarded_source_index'], 2)

    def test_irregular_sampling_retained_and_rates_use_time(self):
        """Off-hour observations remain at original times; rates use actual elapsed hours."""
        records = [(self.start, 10., {}), (self.start+timedelta(minutes=30), 15., {}),
                   (self.start+timedelta(hours=1), 20., {})]
        rows, _, _, changes = prepare_series(records, self.start, self.start+timedelta(hours=1), .01)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(changes), 2)
        self.assertAlmostEqual(changes[0]['rate_m3_m3_per_hour'], .1)

    def test_units_and_flags_do_not_clip_values(self):
        """Percent conversion masks exact zero, retains suspect/extreme values and flags bounds."""
        rows, events, _, _ = prepare_series(self.records([0., 20., -1., 110.]), self.start,
                                            self.start+timedelta(hours=3), .01)
        self.assertTrue(math.isnan(rows[0]['soil_moisture_m3_m3']))
        self.assertIn('zero_value', rows[0]['reasons'])
        self.assertEqual([r['soil_moisture_m3_m3'] for r in rows[1:]], [.2, -.01, 1.1])
        self.assertEqual([r['outside_physical_fraction_bounds'] for r in rows], [False,False,True,True])
        self.assertEqual(rows[1]['provider_flags'], {'qcFlag':'S'})
        self.assertEqual(len(events), 3)

    def test_ambiguous_units_keep_raw_and_constant_diagnostic(self):
        """Unresolved scale never becomes invented m3/m3, but raw flatlines are recorded."""
        rows, _, periods, _ = prepare_series(self.records([.19]*13), self.start,
                                             self.start+timedelta(hours=12), None)
        self.assertTrue(all(math.isnan(r['soil_moisture_m3_m3']) for r in rows))
        self.assertEqual(periods[0]['raw_value'], .19)
        self.assertIn('unit_unresolved', rows[0]['reasons'])
        self.assertIn('constant_period_12h_mask', rows[0]['reasons'])

    def test_invalid_time_and_unapproved_settings_fail(self):
        """Naive timestamps, arbitrary scales and unapproved constant thresholds fail."""
        for factor, threshold in ((100.,12),(1.,24)):
            with self.assertRaises(ValueError):
                prepare_series([], self.start, self.start, factor, threshold)
        with self.assertRaises(ValueError):
            prepare_series([(self.start.replace(tzinfo=None), .2, {})], self.start, self.start, 1.)

    def test_offline_pipeline_and_source_identity(self):
        """Frozen cache produces logged NaNs, refuses overwrite and rejects changed source identity."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root/'cache'; cache.mkdir()
            inventory = dict(study_window_utc={'start':self.start.isoformat(),
                             'end':(self.start+timedelta(hours=12)).isoformat()}, stations=[dict(
                             id='USCRN.04136', network='USCRN', name='Synthetic Spokane',
                             preferred_depth_cm=5., latitude=47.42, longitude=-117.53)])
            from insitu.coverage import requests_for, digest
            address = requests_for(inventory['stations'][0], self.start, self.start+timedelta(hours=12))[0]
            lines = []
            for i in range(13):
                fields = ['0']*38
                fields[:3] = ['04136', '20260617', f'{i:02}00']
                fields[6:8] = ['-117.53', '47.42']; fields[28] = '.2'
                lines.append(' '.join(fields))
            body = '\n'.join(lines)
            response = dict(url=address, retrieved_at_utc=self.start.isoformat(),
                            sha256=hashlib.sha256(body.encode()).hexdigest(), body=body)
            (cache/(hashlib.sha256(address.encode()).hexdigest()+'.json')).write_text(json.dumps(response))
            source = root/'inventory.json'; source.write_text(json.dumps(inventory))
            coverage = root/'coverage.json'
            audit = dict(inventory_sha256=digest(source), stations=[dict(id='USCRN.04136', requests=[address])],
                         responses={address:{'sha256':response['sha256']}})
            coverage.write_text(json.dumps(audit))
            result = run(source, coverage, cache, root/'out')
            self.assertEqual(result['state'], 'prepared')
            self.assertEqual(result['constant_period_policy'], 'mask_all_members')
            self.assertNotIn('constant period', ' '.join(result['pending']).lower())
            self.assertEqual(result['stations'][0]['finite_prepared'], 0)
            self.assertEqual(len(result['stations'][0]['constant_periods']), 1)
            with (root/'out/prepared.csv').open() as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(all(row['soil_moisture_m3_m3']=='NaN' for row in rows))
            self.assertTrue(all(float(row['raw_value'])==.2 for row in rows))
            self.assertEqual(len((root/'out/changes.jsonl').read_text().splitlines()), 13)
            self.assertEqual(result['outputs']['review.md'], digest(root/'out/review.md'))
            with self.assertRaises(FileExistsError):
                run(source, coverage, cache, root/'out')
            audit['responses'][address]['sha256'] = '0'*64
            coverage.write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError, 'differs from frozen'):
                run(source, coverage, cache, root/'other')
            self.assertFalse((root/'other').exists())


if __name__ == '__main__':
    unittest.main()
