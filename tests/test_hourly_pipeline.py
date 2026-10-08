import csv
import gzip
import json
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import crawler
import hourly_pipeline as pipeline


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=crawler.KST)


class MemoryStore:
    commit = "test-source-commit"

    def __init__(self):
        self.data = {}

    def append(self, path, content, message):
        self.data[path] = json.loads(gzip.decompress(content))
        return path

    def records(self):
        return sorted(self.data)

    def read(self, path):
        return self.data[path]

    def add(self, value, totals, status="complete"):
        at = moment(value)
        path = f"raw/{at:%Y/%m/%d}/{at:%H%M%S}.json.gz"
        self.data[path] = {"started_at": at.isoformat(), "finished_at": at.isoformat(),
                           "status": status, "pages": [],
                           "members": [{"rank": i, "name": name, "steps": steps}
                                       for i, (name, steps) in enumerate(totals.items(), 1)]}
        return path


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.override = patch.object(crawler, "DATA_DIR", self.data)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.store = MemoryStore()

    def baseline(self, day, values, period=None):
        path = pipeline.checkpoint_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"members": {name: {"measured_total": value,
                         "period": period or day.strftime("%Y-%m"), "quality": "confirmed"}
                         for name, value in values.items()}}), encoding="utf-8")

    def test_midnight_closes_previous_day_not_midnight_sample(self):
        self.baseline(date(2026, 10, 7), {"테스트": 100})
        self.store.add("2026-10-08T22:00:00", {"테스트": 200})
        source = self.store.add("2026-10-08T23:00:00", {"테스트": 250})
        self.store.add("2026-10-09T00:00:00", {"테스트": 300})
        self.assertEqual(pipeline.finalize_closed_days(self.store, moment("2026-10-09T00:02:00")), 1)
        output = json.loads(pipeline.checkpoint_path(date(2026, 10, 8)).read_text())
        self.assertEqual(output["source"], source)
        self.assertEqual(output["status"], "confirmed")
        with crawler.daily_snapshot_path(date(2026, 10, 8)).open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["오늘걸음수"], "150")
        before = {p: p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        self.assertEqual(pipeline.finalize_closed_days(self.store, moment("2026-10-09T01:00:00")), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.data.rglob('*') if p.is_file()})

    def test_hourly_run_does_not_publish_open_day(self):
        self.store.add("2026-10-08T13:00:00", {"테스트": 100})
        self.assertEqual(pipeline.finalize_closed_days(self.store, moment("2026-10-08T14:00:00")), 0)
        self.assertFalse((self.data / "latest.csv").exists())

    def test_summary_failure_does_not_publish_partial_files(self):
        self.baseline(date(2026, 10, 7), {"테스트": 100})
        self.store.add("2026-10-08T23:00:00", {"테스트": 250})
        before = {p: p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        with patch.object(crawler, "rebuild_season_summary", side_effect=RuntimeError("invalid aggregation")):
            with self.assertRaises(RuntimeError):
                pipeline.finalize_closed_days(self.store, moment("2026-10-09T00:00:00"))
        self.assertEqual(before, {p: p.read_bytes() for p in self.data.rglob('*') if p.is_file()})
        self.assertEqual(crawler.DATA_DIR, self.data)

    def test_failed_last_sample_falls_back_and_marks_stale(self):
        self.baseline(date(2026, 10, 7), {"테스트": 100})
        good = self.store.add("2026-10-08T21:00:00", {"테스트": 150})
        self.store.add("2026-10-08T23:00:00", {"테스트": 999}, status="failed")
        record, members, _ = pipeline.summarize_day(self.store, date(2026, 10, 8), self.store.records(), moment("2026-10-09T00:00:00"))
        self.assertEqual(record["source"], good)
        self.assertEqual(record["status"], "provisional")
        self.assertEqual(members[0]["quality"], "stale")
        self.assertEqual(members[0]["contribution"], 50)

    def test_month_carryover_held_then_collective_reset_is_period_total(self):
        names = {f"사람{i}": 1000 for i in range(6)}
        self.baseline(date(2026, 10, 31), names)
        self.store.add("2026-11-01T23:00:00", {name: 1100 for name in names})
        pipeline.finalize_closed_days(self.store, moment("2026-11-02T00:00:00"))
        pending = json.loads(pipeline.checkpoint_path(date(2026, 11, 1)).read_text())
        self.assertTrue(all(m["quality"] == "pending_month" for m in pending["members"].values()))
        self.store.add("2026-11-02T23:00:00", {name: 500 for name in names})
        pipeline.finalize_closed_days(self.store, moment("2026-11-03T00:00:00"))
        with crawler.daily_snapshot_path(date(2026, 11, 2)).open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["오늘걸음수"], "")
        self.assertEqual(row["집계반영분"], "500")
        self.assertEqual(row["월집계누적"], "500")
        self.assertEqual(row["집계상태"], "inferred_period_total")
        self.store.add("2026-11-03T23:00:00", {name: 700 for name in names})
        pipeline.finalize_closed_days(self.store, moment("2026-11-04T00:00:00"))
        with crawler.daily_snapshot_path(date(2026, 11, 3)).open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["월집계누적"], "700")
        self.assertEqual(row["오늘걸음수"], "200")

    def test_one_member_decrease_does_not_reset_everyone(self):
        self.baseline(date(2026, 10, 31), {f"사람{i}": 1000 for i in range(6)})
        totals = {f"사람{i}": (100 if i == 0 else 1100) for i in range(6)}
        self.store.add("2026-11-01T23:00:00", totals)
        _, members, adjustments = pipeline.summarize_day(self.store, date(2026, 11, 1), self.store.records(), moment("2026-11-02T00:00:00"))
        self.assertFalse(adjustments)
        self.assertTrue(all(m["daily_steps"] is None for m in members))

    def test_new_member_gets_baseline_and_next_day_difference(self):
        people = {f"사람{i}": 100 for i in range(6)}
        self.baseline(date(2026, 10, 7), people)
        self.store.add("2026-10-08T23:00:00", dict(people, 신규=200))
        pipeline.finalize_closed_days(self.store, moment("2026-10-09T00:00:00"))
        first = json.loads(pipeline.checkpoint_path(date(2026, 10, 8)).read_text())
        self.assertEqual(first['members']['신규']['quality'], 'new_baseline')
        self.store.add("2026-10-09T23:00:00", dict(people, 신규=250))
        pipeline.finalize_closed_days(self.store, moment("2026-10-10T00:00:00"))
        with crawler.daily_snapshot_path(date(2026, 10, 9)).open(encoding="utf-8-sig") as f:
            row = next(row for row in csv.DictReader(f) if row['이름'] == '신규')
        self.assertEqual(row['오늘걸음수'], '50')

    def test_initial_collection_gap_uses_legacy_baseline_as_period_amount(self):
        crawler.save_daily_csv([{"rank": 1, "name": "테스트", "daily_steps": 100, "measured_total": 100,
                                 "monthly_total": 100}], date(2026, 10, 7), moment("2026-10-07T23:00:00"))
        self.store.add("2026-10-09T23:00:00", {"테스트": 250})
        pipeline.finalize_closed_days(self.store, moment("2026-10-10T00:00:00"))
        with crawler.daily_snapshot_path(date(2026, 10, 9)).open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["오늘걸음수"], "")
        self.assertEqual(row["집계반영분"], "150")
        self.assertEqual(row["집계상태"], "gap_total")

    def test_late_run_backfills_missing_day_and_marks_gap(self):
        self.baseline(date(2026, 10, 7), {"테스트": 100})
        self.store.add("2026-10-08T23:00:00", {"테스트": 150})
        self.store.add("2026-10-10T23:00:00", {"테스트": 300})
        self.assertEqual(pipeline.finalize_closed_days(self.store, moment("2026-10-11T03:00:00")), 3)
        missing = json.loads(pipeline.checkpoint_path(date(2026, 10, 9)).read_text())
        self.assertEqual(missing["status"], "missing")
        with crawler.daily_snapshot_path(date(2026, 10, 10)).open(encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["오늘걸음수"], "")
        self.assertEqual(row["집계반영분"], "150")

    def test_failed_collection_keeps_partial_pages(self):
        def failed(trace):
            trace.append({"anchor": 0, "body": '{"success":true}'})
            raise RuntimeError("second page failed")
        with patch.object(crawler, "crawl_pacer_data", side_effect=failed), patch.object(crawler, "get_kst_now", return_value=moment("2026-10-08T14:00:01")):
            observation = pipeline.collect(self.store, moment("2026-10-08T14:00:00"))
        self.assertEqual(observation["status"], "failed")
        self.assertEqual(len(next(iter(self.store.data.values()))["pages"]), 1)

    def test_duplicate_names_are_archived_but_not_aggregated(self):
        members = [{"rank": 1, "name": "동명", "steps": 100}, {"rank": 2, "name": "동명", "steps": 0},
                   {"rank": 3, "name": "테스트", "steps": 250}]
        self.baseline(date(2026, 10, 7), {"동명": 50, "테스트": 200})
        with patch.object(crawler, "crawl_pacer_data", return_value=members), patch.object(crawler, "get_kst_now", return_value=moment("2026-10-08T14:00:01")):
            observation = pipeline.collect(self.store, moment("2026-10-08T14:00:00"))
        self.assertEqual(observation["status"], "complete")
        self.assertEqual(observation["ambiguous_names"], ["동명"])
        self.assertEqual(len(observation["members"]), 3)
        checkpoint, results, _ = pipeline.summarize_day(self.store, date(2026, 10, 8), self.store.records(), moment("2026-10-09T00:00:00"))
        by_name = {member["name"]: member for member in results}
        self.assertEqual(by_name["테스트"]["daily_steps"], 50)
        self.assertIsNone(by_name["동명"]["daily_steps"])
        self.assertIsNone(by_name["동명"]["contribution"])
        self.assertEqual(checkpoint["members"]["동명"]["measured_total"], 50)

    def test_legacy_duplicate_failure_is_reusable_but_partial_failure_is_not(self):
        record = {"status": "failed", "error": "빈 결과 또는 중복 이름: 일별 집계에 사용하지 않습니다.",
                  "members": [{"name": "동명", "steps": 100}] * 2,
                  "pages": [{"status_code": 200, "body": json.dumps({"success": True, "data": {"rank_list": [], "paging": {"has_more": False}}})}]}
        self.assertTrue(pipeline.usable_observation(record))
        record["pages"][0]["status_code"] = 500
        self.assertFalse(pipeline.usable_observation(record))
        record["pages"][0]["status_code"] = 200
        record["pages"][0]["body"] = json.dumps({"success": True, "data": {"rank_list": [1], "paging": {"has_more": True}}})
        self.assertFalse(pipeline.usable_observation(record))

    def test_observation_changes_show_update_time_window(self):
        self.store.add("2026-10-08T13:00:00", {"테스트": 100})
        with patch.object(crawler, "crawl_pacer_data", return_value=[{"rank": 1, "name": "테스트", "steps": 150}]), patch.object(crawler, "get_kst_now", return_value=moment("2026-10-08T14:00:01")):
            observation = pipeline.collect(self.store, moment("2026-10-08T14:00:00"))
        self.assertEqual(observation["changes_since"]["observed_at"], moment("2026-10-08T13:00:00").isoformat())
        self.assertEqual(observation["changes"], [{"name": "테스트", "previous": 100, "current": 150}])

    def test_legacy_rebuild_preserves_hourly_checkpoints(self):
        import rebuild_statistics
        self.baseline(date(2026, 10, 7), {"테스트": 100})
        self.store.add("2026-10-08T23:00:00", {"테스트": 250})
        pipeline.finalize_closed_days(self.store, moment("2026-10-09T00:00:00"))
        before = crawler.daily_snapshot_path(date(2026, 10, 8)).read_bytes()
        rebuild_statistics.rebuild()
        self.assertEqual(crawler.daily_snapshot_path(date(2026, 10, 8)).read_bytes(), before)

    def test_crawler_retains_full_response_for_each_page(self):
        first = {"success": True, "data": {"rank_list": [{"rank": 1, "display_text": {"main": "테스트"}, "display_score_text": "100", "uid": "stable-id"}], "paging": {"has_more": True}}}
        second = {"success": True, "data": {"rank_list": [], "paging": {"has_more": False}}}
        from unittest.mock import MagicMock
        session = MagicMock()
        responses = []
        for payload in (first, second):
            response = MagicMock(status_code=200, text=json.dumps(payload))
            response.json.return_value = payload
            responses.append(response)
        session.get.side_effect = responses
        trace = []
        with patch.object(crawler, "build_http_session", return_value=session):
            members = crawler.crawl_pacer_data(trace)
        self.assertEqual(len(trace), 2)
        self.assertEqual(json.loads(trace[0]["body"])["data"]["rank_list"][0]["uid"], "stable-id")
        self.assertEqual(members[0]["steps"], 100)

    def test_period_amount_before_season_start_is_not_counted(self):
        (self.data / "config.json").write_text(json.dumps({"season": {"name": "시즌", "startDate": "2026-11-02", "endDate": "2026-11-30"}, "teams": []}))
        names = {f"사람{i}": 1000 for i in range(6)}
        self.baseline(date(2026, 10, 31), names)
        self.store.add("2026-11-01T23:00:00", names)
        self.store.add("2026-11-02T23:00:00", {name: 500 for name in names})
        pipeline.finalize_closed_days(self.store, moment("2026-11-03T00:00:00"))
        with (self.data / "season.csv").open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(all(r["시즌누적"] == "0" for r in rows))


class GitStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.remote = root / "remote.git"
        self.repo = root / "checkout"
        self.git_cmd("init", "--bare", str(self.remote))
        self.git_cmd("init", "-b", "main", str(self.repo))
        self.git_cmd("-C", str(self.repo), "config", "user.name", "Test")
        self.git_cmd("-C", str(self.repo), "config", "user.email", "test@example.com")
        (self.repo / "source.txt").write_text("keep main intact")
        self.git_cmd("-C", str(self.repo), "add", "source.txt")
        self.git_cmd("-C", str(self.repo), "commit", "-m", "initial")
        self.git_cmd("-C", str(self.repo), "remote", "add", "origin", str(self.remote))
        self.git_cmd("-C", str(self.repo), "push", "origin", "main")

    @staticmethod
    def git_cmd(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)

    def test_storage_only_branch_preserves_main_and_index(self):
        head = self.git_cmd("-C", str(self.repo), "rev-parse", "HEAD")
        (self.repo / "source.txt").write_text("staged local changes")
        self.git_cmd("-C", str(self.repo), "add", "source.txt")
        index = self.git_cmd("-C", str(self.repo), "write-tree")
        store = pipeline.MeasurementStore(self.repo)
        path = "raw/2026/10/08/test.json.gz"
        content = gzip.compress(json.dumps({"status": "failed"}).encode())
        store.append(path, content, "observation")
        self.assertEqual(store.read(path)["status"], "failed")
        self.assertEqual(store.records(), [path])
        self.assertEqual(self.git_cmd("-C", str(self.repo), "rev-parse", "HEAD"), head)
        self.assertEqual(self.git_cmd("-C", str(self.repo), "write-tree"), index)
        self.assertEqual((self.repo / "source.txt").read_text(), "staged local changes")
        with self.assertRaises(RuntimeError):
            store.append(path, content, "overwrite")
        next_store = pipeline.MeasurementStore(self.repo)
        next_store.append("raw/2026/10/08/next.json.gz", content, "next observation")
        self.assertEqual(len(next_store.records()), 2)
        self.assertNotIn("source.txt", self.git_cmd("-C", str(self.repo), "ls-tree", "-r", "--name-only", next_store.commit).decode())

    def test_competing_push_cannot_overwrite_archive(self):
        first = pipeline.MeasurementStore(self.repo)
        first.append("README.md", b"archive", "bootstrap")
        second = pipeline.MeasurementStore(self.repo)
        second.append("other.txt", b"second", "competing update")
        with self.assertRaises(subprocess.CalledProcessError):
            first.append("local.txt", b"first", "outdated update")
        latest = pipeline.MeasurementStore(self.repo)
        self.assertEqual(latest.git("show", f"{latest.commit}:other.txt").stdout, b"second")


if __name__ == "__main__":
    unittest.main()
