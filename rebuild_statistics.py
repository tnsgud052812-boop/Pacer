"""보관된 측정 원본으로 일별·월별·시즌별 통계를 다시 계산한다.

실행: python rebuild_statistics.py
네트워크 요청 없이 data/의 생성 통계만 갱신한다. 측정 원본은 보존한다.
"""

import csv
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
