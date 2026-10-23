"""Synthetic checks for the R28 availability audit, independent of live downloads."""
import json
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'scripts'), str(Path(__file__).resolve().parents[1]/'src')]
from insitu.coverage import summarize, awdb_records, uscrn_records, agrimet_records, requests_for, crosscheck_empty_awdb

class CoverageTests(unittest.TestCase):
    def test_hourly_bounds_missing_flags_and_gaps(self):
        """Partial-day bounds count labels exactly; suspect finite values stay present."""
        start=datetime(2026,6,17,2,53,tzinfo=timezone.utc)
        end=datetime(2026,6,17,6,39,tzinfo=timezone.utc)
        records=[(datetime(2026,6,17,h,tzinfo=timezone.utc),v,{'qcFlag':'S'}) for h,v in [(3,0),(4,None),(5,None),(6,-1)]]
        result=summarize(records,start,end)
        self.assertEqual((result['expected_hours'],result['finite_hours'],result['longest_missing_hours']),(4,2,2))
        self.assertEqual(result['minimum_reported'],-1)
        self.assertEqual(result['finite_percent'],50)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            summarize(records+records[:1],start,end)

    def test_awdb_exact_channel_fixed_standard_time(self):
        """NRCS summer hours retain UTC−8 and suspect flags without a DST shift."""
        body=json.dumps([{'stationTriplet':'2021:WA:SCAN','data':[{'stationElement':{'elementCode':'SMS','heightDepth':-2,'ordinal':1,'durationName':'HOURLY','storedUnitCode':'pct'},'values':[{'date':'2026-06-17 00:00','value':1.2,'qcFlag':'S','qaFlag':'R'}]}]}])
        rows,meta=awdb_records(body,{'id':'2021:WA:SCAN','catalog_time_zone_utc_offset_hours':-8})
        self.assertEqual(rows[0][0].hour,8)
        self.assertEqual(rows[0][2]['qcFlag'],'S')
        self.assertEqual(rows[0][1],1.2)

    def test_uscrn_missing_code_and_identity(self):
        """NOAA shallow missing codes are decoded and different station IDs fail."""
        row=['0']*38;row[:3]=['04136','20260617','0300'];row[6:8]=['-117.53','47.42'];row[28]='-99.000'
        rows,meta=uscrn_records(' '.join(row).encode())
        self.assertIsNone(rows[0][1])
        self.assertEqual(meta['data_coordinates_lon_lat'],[(-117.53,47.42)])
        row[0]='99999'
        with self.assertRaises(ValueError):uscrn_records(' '.join(row).encode())

    def test_agrimet_dst_raw_units_flags(self):
        """Odessa summer local labels convert with DST, preserving raw scale/flags."""
        body=b'BEGIN DATA\nDATE TIME, ODSW XSM2\n06/17/2026 00:00,0.19E\n06/17/2026 01:00,---\nEND DATA'
        rows,meta=agrimet_records(body)
        self.assertEqual(rows[0][0].hour,7)
        self.assertEqual(rows[0][1],.19)
        self.assertEqual(rows[0][2]['suffix'],'E')
        self.assertIsNone(rows[1][1])
        with self.assertRaises(ValueError):agrimet_records(body.replace(b'0.19E',b'garbage'))

    def test_requests_do_not_filter_suspect_or_change_depth(self):
        """Audit requests preserve suspect records and the inventoried shallow depth."""
        start=datetime(2026,6,17,tzinfo=timezone.utc);end=datetime(2026,10,10,tzinfo=timezone.utc)
        address=requests_for({'id':'2021:WA:SCAN','network':'SCAN'},start,end)[0]
        self.assertIn('returnSuspectData=true',address)
        self.assertIn('elements=SMS%3A-2%3A1',address)
        self.assertIn('beginDate=2026-06-16',address)

    def test_empty_response_remains_distinct_from_measured_zero(self):
        """Empty hourly/daily responses retain channel evidence, not a zero-valued series."""
        class Cache:
            def record(self, name, address):
                if '/stations?' in address:
                    return json.dumps([{'stationTriplet':'507:WA:SNTL','stationElements':[{
                        'elementCode':'SMS','heightDepth':-2,'ordinal':1,'durationName':'HOURLY'}]}]).encode()
                return b'[]'
        rows=[{'id':'507:WA:SNTL','requests':[],'empty_hourly_response':True}]
        start=datetime(2026,6,17,tzinfo=timezone.utc);end=datetime(2026,10,10,tzinfo=timezone.utc)
        crosscheck_empty_awdb(Cache(),rows,start,end)
        self.assertEqual(rows[0]['status'],'no_records_returned')
        self.assertNotIn('coverage',rows[0])
        self.assertIsNone(rows[0]['empty_response_crosscheck']['daily_response'])
        self.assertEqual(len(rows[0]['empty_response_crosscheck']['current_catalog_channels']),1)

if __name__=='__main__': unittest.main()
