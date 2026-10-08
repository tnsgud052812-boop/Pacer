"""시간별 원본은 measurements 브랜치, 종료된 날짜의 통계는 data/에 저장."""

import argparse
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

import crawler


class MeasurementStore:
    """별도 Git index로 원본만 커밋한다. main의 체크아웃과 index는 유지한다."""

    def __init__(self, repo, remote="origin", branch="measurements"):
        self.repo = Path(repo)
        self.remote = remote
        self.branch = branch
        probe = self.git("ls-remote", "--exit-code", remote, f"refs/heads/{branch}", check=False)
        if probe.returncode == 2:
            self.commit = None
        elif probe.returncode == 0:
            self.git("fetch", "--no-tags", remote, f"refs/heads/{branch}")
            self.commit = self.git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
        else:
            raise RuntimeError("원본 브랜치 접근 실패; Git 오류를 확인하세요.")

    def git(self, *args, input=None, env=None, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=check, env=env)

    def append(self, path, content, message):
        if self.commit and self.git("cat-file", "-e", f"{self.commit}:{path}", check=False).returncode == 0:
            raise RuntimeError(f"원본 덮어쓰기 금지: {path}")
        blob = self.git("hash-object", "-w", "--stdin", input=content).stdout.decode().strip()
        with tempfile.TemporaryDirectory(prefix="pacer-index-") as directory:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"),
                       GIT_AUTHOR_NAME="Pacer collector", GIT_AUTHOR_EMAIL="pacer@users.noreply.github.com",
                       GIT_COMMITTER_NAME="Pacer collector", GIT_COMMITTER_EMAIL="pacer@users.noreply.github.com")
            self.git("read-tree", self.commit if self.commit else "--empty", env=env)
            self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self.git("write-tree", env=env).stdout.decode().strip()
            parent = ["-p", self.commit] if self.commit else []
            commit = self.git("commit-tree", tree, *parent, input=(message + "\n").encode(), env=env).stdout.decode().strip()
        # 강제 push하지 않는다. 경쟁 업데이트는 실패시켜 기존 원본을 보호한다.
        self.git("push", self.remote, f"{commit}:refs/heads/{self.branch}")
        self.commit = commit
        return path

    def records(self):
        if not self.commit:
            return []
        listing = self.git("ls-tree", "-r", "--name-only", self.commit, "raw/").stdout.decode().splitlines()
        return sorted(path for path in listing if path.endswith(".json.gz"))

    def read(self, path):
        return json.loads(gzip.decompress(self.git("show", f"{self.commit}:{path}").stdout))


def collect(store, now=None):
    started = now or crawler.get_kst_now()
    observation = {"version": 1, "started_at": started.isoformat(), "pages": [], "members": []}
    try:
        observation["members"] = crawler.crawl_pacer_data(trace=observation["pages"])
        names = [member["name"] for member in observation["members"]]
        if not names or len(names) != len(set(names)):
            raise RuntimeError("빈 결과 또는 중복 이름: 일별 집계에 사용하지 않습니다.")
        observation["status"] = "complete"
    except Exception as error:
        observation["status"] = "failed"
        observation["error"] = str(error)
    observation["finished_at"] = crawler.get_kst_now().isoformat()
    if observation["status"] == "complete":
        for previous_path in reversed(store.records()):
            previous = store.read(previous_path)
            if previous.get("status") != "complete":
                continue
            old = {member["name"]: member["steps"] for member in previous["members"]}
            observation["changes_since"] = {"source": previous_path, "observed_at": previous["finished_at"]}
            observation["changes"] = [
                {"name": member["name"], "previous": old.get(member["name"]), "current": member["steps"]}
                for member in observation["members"] if old.get(member["name"]) != member["steps"]
            ]
            break
    stamp = started.strftime("%Y%m%dT%H%M%S%f%z")
    run = os.environ.get("GITHUB_RUN_ID", "local")
    path = f"raw/{started:%Y/%m/%d}/{stamp}_{run}_{uuid.uuid4().hex[:12]}.json.gz"
    payload = gzip.compress(json.dumps(observation, ensure_ascii=False).encode(), mtime=0)
    store.append(path, payload, f"Pacer observation {started.isoformat()} ({observation['status']})")
    return observation


def checkpoint_path(day):
    return crawler.DATA_DIR / "checkpoints" / f"{day.isoformat()}.json"


def previous_baseline(day):
    previous = day - timedelta(days=1)
    path = checkpoint_path(previous)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("members", {})
    # 전환 첫날에는 기존 일별 원본을 기준점으로 이어받는다.
    values = crawler.load_previous_day_totals(day)
    quality = "legacy"
    if not values:
        snapshots = sorted((crawler.DATA_DIR / "daily").rglob("*.csv"), key=lambda p: p.stem, reverse=True)
        for snapshot in snapshots:
            snapshot_day = crawler.date.fromisoformat(snapshot.stem)
            if snapshot_day >= day:
                continue
            values = crawler.load_previous_day_totals(snapshot_day + timedelta(days=1))
            if values:
                previous = snapshot_day
                quality = "missing"
                break
    return {name: {"measured_total": total, "period": previous.strftime("%Y-%m"), "quality": quality}
            for name, total in values.items()}


def summarize_day(store, day, paths, finalized_at):
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=crawler.KST)
    records = [(path, store.read(path)) for path in paths]
    valid = [(path, record) for path, record in records
             if record.get("status") == "complete"
             and datetime.fromisoformat(record["started_at"]).astimezone(crawler.KST).date() == day
             and datetime.fromisoformat(record["finished_at"]) < end]
    valid.sort(key=lambda pair: pair[1]["finished_at"])
    previous = previous_baseline(day)
    month = day.strftime("%Y-%m")
    correction_file = crawler.DATA_DIR / "statistics_corrections.json"
    corrections = json.loads(correction_file.read_text(encoding="utf-8")) if correction_file.exists() else {}
    correction = corrections.get(day.isoformat(), {})
    period_members = dict(previous)
    # 月 전환 시 숫자만으로 확인되는 초기화는 추정으로 기록한다.
    inferred = set()
    last_values = {name: state["measured_total"] for name, state in previous.items()}
    for _path, observation in valid:
        comparable = [m for m in observation["members"] if last_values.get(m["name"], 0) > 0]
        drops = [m for m in comparable if m["steps"] < last_values[m["name"]]]
        pending = [m for m in comparable if period_members.get(m["name"], {}).get("period") != month]
        # 소수 개인의 수정값을 전체 월 초기화로 오인하지 않는다.
        reset_candidate = len(comparable) >= 5 and len(drops) / len(comparable) >= 0.6 and bool(pending)
        for member in observation["members"]:
            name = member["name"]
            if reset_candidate and (any(m["name"] == name for m in drops)
                                    or member["steps"] == last_values.get(name) == 0):
                period_members[name] = {"period": month, "quality": "inferred"}
                inferred.add(name)
            last_values[name] = member["steps"]
    selected = valid[-1] if valid else None
    measured_at = datetime.fromisoformat(selected[1]["finished_at"]) if selected else None
    stale = measured_at is None or end - measured_at > timedelta(minutes=90)
    totals = crawler.load_monthly_totals(day)
    result = []
    states = {}
    adjustments = []
    baseline_gap = False
    known_periods = [state for state in period_members.values() if state.get("period") == month]
    cohort_has_current_month = len(known_periods) >= 5 and len(known_periods) > len(period_members) / 2
    for member in selected[1]["members"] if selected else []:
        name, total = member["name"], member["steps"]
        old = previous.get(name)
        state = period_members.get(name, {})
        period = state.get("period")
        quality = "confirmed"
        daily = contribution = None
        if correction.get("exclude"):
            quality = "excluded"
        elif correction.get("monthly_reset") or name in inferred or (old and old.get("period") != month and period == month):
            # 첫 새 달 누적값에는 앞선 날의 활동이 포함될 수 있다.
            period = month
            quality = "period_total" if correction.get("monthly_reset") else "inferred_period_total"
            contribution = max(0, total - totals.get(name, 0))
            adjustments.append({"name": name, "amount": contribution, "period_start": day.replace(day=1).isoformat(),
                                "observed_at": measured_at.isoformat(), "quality": quality})
        elif not old and cohort_has_current_month:
            # 신규 참가자는 첫 측정을 기준점으로만 사용한다. 다음 날부터 차이를 계산한다.
            period = month
            quality = "new_baseline"
        elif old and old.get("period") == month and total >= old["measured_total"]:
            contribution = total - old["measured_total"]
            if old.get("quality") in ("missing", "stale"):
                quality = "gap_total"
                baseline_gap = True
            else:
                daily = contribution
                if old.get("quality") in ("inferred", "inferred_period_total", "inferred_continuity"):
                    quality = "inferred_continuity"
        else:
            quality = "pending_month" if period != month else "unknown"
        if stale and quality == "confirmed":
            quality = "stale"
        states[name] = {"measured_total": total, "period": period, "quality": quality}
        result.append({"rank": member["rank"], "name": name, "measured_total": total,
                       "daily_steps": daily, "contribution": contribution, "quality": quality,
                       "monthly_total": totals.get(name, 0) + (contribution or 0)})
    if selected is None:
        states = {name: dict(state, quality="missing") for name, state in previous.items()}
        # 빈 날짜도 표시해 이전 월/날짜를 정상 최신값으로 오인하지 않게 한다.
    checkpoint = {"version": 1, "date": day.isoformat(), "cutoff": end.isoformat(),
                  "finalized_at": finalized_at.isoformat(), "source": selected[0] if selected else None,
                  "source_commit": store.commit, "measured_at": measured_at.isoformat() if measured_at else None,
                  "observation_count": len(records), "complete_observation_count": len(valid),
                  "status": "missing" if not selected else "provisional" if stale or baseline_gap or any(m["quality"] != "confirmed" for m in result) else "confirmed",
                  "members": states}
    return checkpoint, result, adjustments


def finalize_closed_days(store, now):
    # 집계 도중 오류가 나면 공개 data/에 일부 결과만 남기지 않는다.
    original = crawler.DATA_DIR
    with tempfile.TemporaryDirectory(prefix="pacer-summary-") as directory:
        staged = Path(directory) / "data"
        shutil.copytree(original, staged)
        try:
            crawler.DATA_DIR = staged
            count = _finalize_closed_days(store, now)
        finally:
            crawler.DATA_DIR = original
        for source in staged.rglob("*"):
            if source.is_file():
                destination = original / source.relative_to(staged)
                if not destination.exists() or destination.read_bytes() != source.read_bytes():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
        return count


def _finalize_closed_days(store, now):
    paths_by_day = {}
    for path in store.records():
        parts = path.split("/")
        day = crawler.date(int(parts[1]), int(parts[2]), int(parts[3]))
        if day < now.date():
            paths_by_day.setdefault(day, []).append(path)
    if not paths_by_day:
        return 0
    finalized = 0
    # 누락된 자정 실행은 다음 실행에서 보충한다. 진행 중인 날은 절대 집계하지 않는다.
    for day in crawler.iter_dates(min(paths_by_day), now.date() - timedelta(days=1)):
        output = checkpoint_path(day)
        if output.exists():
            continue
        checkpoint, members, adjustments = summarize_day(store, day, paths_by_day.get(day, []), now)
        measured_at = datetime.fromisoformat(checkpoint["measured_at"]) if checkpoint["measured_at"] else datetime.combine(day, time(23, 59, 59), tzinfo=crawler.KST)
        for member in members:
            crawler.update_member_file(member["name"], day, member["daily_steps"], member["monthly_total"])
        crawler.save_daily_csv(members, day, measured_at)
        crawler.save_latest(members, measured_at)
        directory = crawler.DATA_DIR / "adjustments"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{day.isoformat()}.json").write_text(json.dumps(adjustments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        crawler.rebuild_season_summary(day, now, [m["name"] for m in members])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (crawler.DATA_DIR / "checkpoint_status.json").write_text(json.dumps({key: value for key, value in checkpoint.items() if key != "members"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finalized += 1
    return finalized


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="원본 브랜치를 README만으로 초기화")
    parser.add_argument("--report", metavar="YYYY-MM-DD", help="해당 한국 날짜의 시간별 수집/변경 현황 조회")
    args = parser.parse_args()
    store = MeasurementStore(crawler.BASE_DIR)
    if args.report:
        day = crawler.date.fromisoformat(args.report)
        for path in store.records():
            if path.startswith(f"raw/{day:%Y/%m/%d}/"):
                record = store.read(path)
                print(json.dumps({"source": path, "started_at": record["started_at"],
                                  "finished_at": record["finished_at"], "status": record["status"],
                                  "changes_since": record.get("changes_since"),
                                  "changes": record.get("changes", []), "error": record.get("error")}, ensure_ascii=False))
        return
    if args.bootstrap:
        if store.commit is None:
            store.append("README.md", "# Pacer 시간별 측정 원본\n\n한국 시간 기준 raw/YYYY/MM/DD/에 압축 JSON 원본을 추가합니다. main의 hourly_pipeline.py가 관리합니다. 기존 원본을 덮어쓰지 않습니다.\n".encode(), "Initialize hourly measurement archive")
        return
    observation = collect(store)
    count = finalize_closed_days(store, crawler.get_kst_now())
    print(f"원본 저장: {observation['status']} / 종료 날짜 집계: {count}개")
    if observation["status"] != "complete":
        raise SystemExit("수집 실패 원본을 보관했습니다. 기존 정상 원본의 종료 날짜 집계는 수행했습니다.")


if __name__ == "__main__":
    main()
