import csv
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import crawler


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = patch.object(crawler, "DATA_DIR", Path(self.directory.name))
        self.override.start()
        self.addCleanup(self.override.stop)

    def calculate(self, day, measured, previous):
        return crawler.calculate_daily_steps(
            [{"rank": 1, "name": "테스트", "steps": measured}], previous, day
        )[0]

    def save(self, day, member):
        crawler.save_daily_csv([member], day, datetime.combine(day, datetime.min.time()))

    def test_first_day_carryover_uses_difference(self):
        result = self.calculate(date(2026, 10, 1), 689851, {"테스트": 643880})
        self.assertEqual(result["daily_steps"], 45971)
        self.assertEqual(result["monthly_total"], 45971)
        self.assertEqual(result["measured_total"], 689851)

    def test_month_reset_on_first_or_second_day(self):
        for day in (1, 2):
            with self.subTest(day=day):
                result = self.calculate(date(2026, 9, day), 10841, {"테스트": 607837})
                self.assertEqual(result["daily_steps"], 10841)

    def test_sum_and_rerun_use_raw_previous_measurement(self):
        first_day = date(2026, 10, 1)
        self.save(first_day, self.calculate(first_day, 1100, {"테스트": 1000}))
        day = date(2026, 10, 2)
        previous = crawler.load_previous_day_totals(day)
        self.assertEqual(previous, {"테스트": 1100})
        result = self.calculate(day, 1150, previous)
        self.assertEqual(result["monthly_total"], 150)
        self.save(day, result)
        self.assertEqual(self.calculate(day, 1150, previous), result)
        crawler.save_latest([result], datetime(2026, 10, 2))
        with (crawler.DATA_DIR / "latest.csv").open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["월누적"], "150")
        self.assertEqual(row["측정누적"], "1150")

    def test_missing_baseline_and_midmonth_decrease_are_unknown(self):
        self.assertIsNone(self.calculate(date(2026, 10, 1), 500000, {})["daily_steps"])
        self.assertIsNone(self.calculate(date(2026, 10, 5), 10, {"테스트": 100})["daily_steps"])

    def test_legacy_snapshot_baseline(self):
        path = crawler.daily_snapshot_path(date(2026, 9, 30))
        path.parent.mkdir(parents=True)
        path.write_text("이름,월간누적\n테스트,643880\n", encoding="utf-8-sig")
        self.assertEqual(crawler.load_previous_day_totals(date(2026, 10, 1)), {"테스트": 643880})

    def test_confirmed_exclusion_and_delayed_reset(self):
        (crawler.DATA_DIR / "statistics_corrections.json").write_text(json.dumps({
            "2026-10-01": {"exclude": True},
            "2026-10-03": {"monthly_reset": True},
        }), encoding="utf-8")
        first = self.calculate(date(2026, 10, 1), 689851, {"테스트": 643880})
        self.assertIsNone(first["daily_steps"])
        self.assertEqual(first["monthly_total"], 0)
        self.save(date(2026, 10, 1), first)
        second = self.calculate(date(2026, 10, 2), 689851, {"테스트": 689851})
        self.save(date(2026, 10, 2), second)
        third = self.calculate(date(2026, 10, 3), 45066, {"테스트": 689851})
        self.assertEqual(third["monthly_total"], 45066)
        self.save(date(2026, 10, 3), third)
        fourth = self.calculate(date(2026, 10, 4), 55066, {"테스트": 45066})
        self.assertEqual(fourth["daily_steps"], 10000)
        self.assertEqual(fourth["monthly_total"], 55066)

    def test_season_sum_crosses_month_without_carryover(self):
        (crawler.DATA_DIR / "config.json").write_text(json.dumps({
            "season": {"name": "테스트 시즌", "startDate": "2026-09-30", "endDate": "2026-10-02"},
            "teams": [{"name": "테스트 팀", "members": ["테스트"]}],
        }), encoding="utf-8")
        for day, measured, previous in [
            (date(2026, 9, 30), 1000, 900),
            (date(2026, 10, 1), 1100, 1000),
            (date(2026, 10, 2), 50, 1100),
        ]:
            self.save(day, self.calculate(day, measured, {"테스트": previous}))
        crawler.rebuild_season_summary(date(2026, 10, 2), datetime(2026, 10, 2), ["테스트"])
        with (crawler.DATA_DIR / "season.csv").open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["시즌누적"], "250")
        status = json.loads((crawler.DATA_DIR / "season_status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["complete"])


if __name__ == "__main__":
    unittest.main()
