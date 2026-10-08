"""보관된 측정 원본으로 일별·월별·시즌별 통계를 다시 계산한다.

실행: python rebuild_statistics.py
네트워크 요청 없이 data/의 생성 통계만 갱신한다. 측정 원본은 보존한다.
"""

import csv
import json
from contextlib import redirect_stdout
from io import StringIO

import crawler


def rebuild():
    snapshots = sorted(crawler.DATA_DIR.glob("daily/**/*.csv"), key=lambda p: p.stem)
    latest_members = None
    latest_time = None
    count = 0
    for path in snapshots:
        run_date = crawler.date.fromisoformat(path.stem)
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        checkpoint = crawler.DATA_DIR / "checkpoints" / f"{run_date.isoformat()}.json"
        if checkpoint.exists():
            # 시간별 파이프라인의 확정 기준점/기간 누적분을 구형 방식으로 덮어쓰지 않는다.
            status = json.loads(checkpoint.read_text(encoding="utf-8"))
            latest_time = crawler.datetime.fromisoformat(status["measured_at"]) if status["measured_at"] else crawler.datetime.combine(run_date, crawler.datetime.min.time()).replace(tzinfo=crawler.KST)
            latest_members = [{
                "rank": crawler.parse_integer(row["순위"]), "name": row["이름"],
                "daily_steps": crawler.parse_optional_integer(row["오늘걸음수"]),
                "measured_total": crawler.parse_integer(row["월간누적"]),
                "monthly_total": crawler.parse_integer(row["월집계누적"]),
                "contribution": crawler.parse_optional_integer(row.get("집계반영분")),
                "quality": row.get("집계상태", "unknown"),
            } for row in rows]
            continue
        if not rows:
            continue
        # 월간누적은 API 측정 원본이며 월집계누적과 구분한다.
        measurements = [
            {
                "rank": crawler.parse_integer(row["순위"]),
                "name": row["이름"],
                "steps": crawler.parse_integer(row.get("측정누적", row.get("월간누적", row.get("월누적")))),
            }
            for row in rows
        ]
        crawl_time = crawler.datetime.fromisoformat(rows[0]["크롤링일시"]).replace(tzinfo=crawler.KST)
        with redirect_stdout(StringIO()):
            previous = crawler.load_previous_day_totals(run_date)
            members = crawler.calculate_daily_steps(measurements, previous, run_date)
            for member in members:
                crawler.update_member_file(member["name"], run_date, member["daily_steps"], member["monthly_total"])
            crawler.save_daily_csv(members, run_date, crawl_time)
        latest_members, latest_time = members, crawl_time
        count += 1
    if latest_members is not None:
        crawler.save_latest(latest_members, latest_time)
        crawler.rebuild_season_summary(latest_time.date(), latest_time, [m["name"] for m in latest_members])
    print(f"통계 재계산 완료: {count}개 일별 스냅샷 (측정 원본 보존)")


if __name__ == "__main__":
    rebuild()
