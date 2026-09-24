"""Uniform timing display must not change the numbers used for matching."""
import csv
import io
import unittest
from contextlib import redirect_stdout
from bluray_map.timeformat import format_duration, readable_timings
from bluray_map.report import emit_csv, human
from test_discsteward import UI


class TimeFormatTests(unittest.TestCase):
    def test_units_rounding_and_long_durations(self):
        for seconds, expected in [(0,'00:00:00.000'), (2550.673125,'00:42:30.673'),
                                  (3661.002,'01:01:01.002'), (59.9995,'00:01:00.000'),
                                  (3599.9995,'01:00:00.000'), (360000,'100:00:00.000')]:
            self.assertEqual(format_duration(seconds),expected)

    def test_unknown_signed_and_tiny_differences(self):
        for value in (None,'bad',float('nan'),float('inf')):
            self.assertEqual(format_duration(value),'Unknown')
        self.assertEqual(format_duration(-1.25),'-00:00:01.250')
        self.assertEqual(format_duration(1.25,signed=True),'+00:00:01.250')
        self.assertEqual(format_duration(.000014,difference=True),'<00:00:00.001')
        self.assertEqual(format_duration(0,difference=True),'00:00:00.000')

    def test_export_keeps_numeric_precision_without_mutating_input(self):
        original={'files':[{'duration_seconds':2550.673125,'difference_seconds':.000014,
                            'container_duration_seconds':None}]}
        result=readable_timings(original)
        self.assertEqual(result['files'][0]['duration_display'],'00:42:30.673')
        self.assertEqual(result['files'][0]['duration_seconds'],2550.673125)
        self.assertEqual(result['files'][0]['difference_display'],'<00:00:00.001')
        self.assertNotIn('duration_display',original['files'][0])
        self.assertEqual(readable_timings(result),result)

    def test_cli_and_csv_use_same_format(self):
        episode={'playlist':'test','duration_seconds':3661.002,'clips':[], 'confidence':80,'reasons':[]}
        data={'disc':{'path':'test'},'playlists':[],'clips':[], 'detected_episodes':[episode],
              'extras':[],'alternate_playlists':[],'warnings':[]}
        output=io.StringIO()
        with redirect_stdout(output): human(data)
        self.assertIn('01:01:01.002',output.getvalue())
        output=io.StringIO(); emit_csv(data,output)
        row=next(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(row['duration'],'01:01:01.002')
        self.assertEqual(row['duration_seconds'],'3661.002')

    def test_text_report_includes_programme_rip_difference_and_unmatched_length(self):
        report={'episodes':[{'episode':1,'playlist':'test','duration_seconds':3661.002,
                'clips':[],'confidence':80,'source':'unverified','matching_rips':[
                {'path':'match.mkv','duration_seconds':3661.002014,'duration_source':'video_track_tag',
                 'difference_seconds':.000014}]}], 'unmatched_rips':['other.mkv'],
                'rip_inventory':[{'path':'other.mkv','duration_seconds':62.125}]}
        text=UI['human_report'](report)
        self.assertIn('01:01:01.002',text)
        self.assertIn('difference <00:00:00.001',text)
        self.assertIn('other.mkv — 00:01:02.125',text)
