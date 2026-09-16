"""Pacer 만보걷기 집계 크롤러.

Pacer의 월 누적 걸음수를 수집하고, 전날 스냅샷과 비교해 당일 걸음수를
계산한다. 같은 날 여러 번 실행해도 항상 전날 스냅샷을 기준으로 다시
계산하므로 당일 걸음수가 축소되지 않는다.
"""

import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 설정
GROUP_ID = 31844011
BASE_URL = "https://www.mypacer.com/api/v1/leaderboard"
REFERER = "https://www.mypacer.com/clubs/1n3qqmrn/-ju-kaentabseu-suwon-gyeonggi-do"
MAX_PAGES = 100
REQUEST_TIMEOUT = 15

# crawler.py가 어느 작업 폴더에서 실행되더라도 같은 data 폴더를 사용한다.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


def get_kst_now():
    """한국 시간 반환"""
    return datetime.now(KST)


def build_http_session() -> requests.Session:
    """일시적인 서버 오류와 요청 제한에 재시도하는 HTTP 세션 생성."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def parse_integer(value) -> int:
    """쉼표 등이 포함된 API 숫자 값을 정수로 변환."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    cleaned = re.sub(r"[^0-9.-]", "", str(value))
    if not cleaned:
        return 0
    return int(float(cleaned))


def parse_optional_integer(value) -> Optional[int]:
    """빈 값이나 N/A는 None으로, 숫자 값은 정수로 변환."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "null", "none", "-"}:
        return None

    cleaned = re.sub(r"[^0-9.-]", "", text)
    if not cleaned:
        return None
    return int(float(cleaned))


def daily_snapshot_path(target_date: date) -> Path:
    """해당 날짜의 일별 스냅샷 경로 반환."""
    month_folder = DATA_DIR / "daily" / f"{target_date.year}년{target_date.month}월"
    return month_folder / f"{target_date.isoformat()}.csv"


def migrate_old_daily_files():
    """기존 daily 파일들을 연월별 폴더로 이동"""
    daily_dir = DATA_DIR / "daily"
    if not daily_dir.exists():
        return
    
    moved_count = 0
    for source_path in daily_dir.iterdir():
        filename = source_path.name
        # 2026-02-01.csv 형식인 파일만 처리
        if source_path.is_file() and filename.endswith(".csv") and len(filename) == 14:
            try:
                # 파일명에서 날짜 추출
                date_str = filename.removesuffix(".csv")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                
                # 연월 폴더 생성
                month_folder = daily_dir / f"{file_date.year}년{file_date.month}월"
                month_folder.mkdir(parents=True, exist_ok=True)
                
                # 파일 이동
                new_path = month_folder / filename
                
                if not new_path.exists():
                    source_path.replace(new_path)
                    moved_count += 1
                    print(f"  이동: {filename} → {month_folder}/")
            except (OSError, ValueError) as error:
                print(f"  기존 파일 이동 건너뜀 ({filename}): {error}")
                continue
    
    if moved_count > 0:
        print(f"📁 기존 파일 {moved_count}개 정리 완료")


def crawl_pacer_data() -> List[Dict]:
    """Pacer API에서 전체 멤버 데이터 크롤링"""
    all_members = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": REFERER
    }
    
    print("크롤링 시작...")
    
    session = build_http_session()
    anchor = 0

    try:
        for _page_number in range(1, MAX_PAGES + 1):
            url = f"{BASE_URL}/{GROUP_ID}?anchor={anchor}"
            response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                raise RuntimeError(f"Pacer API가 실패 응답을 반환했습니다: anchor={anchor}")

            rank_list = data.get("data", {}).get("rank_list", [])
            if not rank_list:
                break

            for item in rank_list:
                name = str(item.get("display_text", {}).get("main", "")).strip()
                if not name:
                    print(f"  경고: 이름이 없는 항목을 제외했습니다 (anchor={anchor})")
                    continue

                all_members.append(
                    {
                        "rank": parse_integer(item.get("rank", 0)),
                        "name": name,
                        "steps": parse_integer(item.get("display_score_text", 0)),
                    }
                )

            print(f"  anchor={anchor}: {len(rank_list)}명 수집")

            if not data.get("data", {}).get("paging", {}).get("has_more"):
                break

            anchor += len(rank_list)
        else:
            raise RuntimeError(
                f"페이지가 {MAX_PAGES}개를 초과했습니다. 무한 반복 방지를 위해 중단합니다."
            )
    except (requests.RequestException, ValueError, RuntimeError) as error:
        # 일부 인원만 저장하면 순위 데이터가 손상되므로 전체 실행을 실패시킨다.
        raise RuntimeError(f"Pacer 데이터 수집 실패 (anchor={anchor}): {error}") from error
    finally:
        session.close()

    seen_names = set()
    duplicate_names = set()
    for member in all_members:
        if member["name"] in seen_names:
            duplicate_names.add(member["name"])
        seen_names.add(member["name"])
    if duplicate_names:
        print(f"⚠️ 동명이인/중복 닉네임 감지: {', '.join(sorted(duplicate_names))}")
    
    print(f"크롤링 완료: 총 {len(all_members)}명")
    return all_members


def load_previous_day_totals(run_date: date) -> Dict[str, int]:
    """전날 일별 스냅샷에서 월 누적 데이터 로드."""
    previous_date = run_date - timedelta(days=1)
    filename = daily_snapshot_path(previous_date)

    if not filename.exists():
        print(f"⚠️ 전날 스냅샷 없음: {filename}")
        return {}

    data = {}
    try:
        with filename.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("이름", "").strip()
                monthly_total = row.get("월간누적", row.get("월누적", ""))
                if name and monthly_total not in (None, ""):
                    data[name] = parse_integer(monthly_total)
    except (OSError, ValueError, csv.Error) as error:
        raise RuntimeError(f"전날 데이터 로드 실패 ({filename}): {error}") from error

    print(f"전날 기준 데이터 로드: {previous_date.isoformat()} / {len(data)}명")
    return data


def safe_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자 제거"""
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name


def update_member_file(
    name: str,
    run_date: date,
    daily_steps: Optional[int],
    monthly_total: int,
):
    """개인별 월간 파일 업데이트"""
    members_dir = DATA_DIR / "members"
    members_dir.mkdir(parents=True, exist_ok=True)

    # 파일명: 홍길동_2026년2월_Data.csv (KST 기준)
    month_str = f"{run_date.year}년{run_date.month}월"
    safe_name = safe_filename(name)
    filename = members_dir / f"{safe_name}_{month_str}_Data.csv"
    date_str = run_date.strftime("%m/%d")

    # 기존 데이터 로드
    existing_data = []
    existing_dates = set()

    if filename.exists():
        with filename.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_data.append(row)
                existing_dates.add(row["날짜"])

    # 오늘 날짜가 이미 있으면 덮어쓰기
    if date_str in existing_dates:
        print(f"  {name}: 오늘 데이터 업데이트")
        existing_data = [row for row in existing_data if row["날짜"] != date_str]
    
    # 새 데이터 추가
    existing_data.append({
        "날짜": date_str,
        "오늘걸음수": daily_steps if daily_steps is not None else "N/A",
        "월누적": monthly_total
    })
    
    # 파일 저장
    with filename.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["날짜", "오늘걸음수", "월누적"])
        writer.writeheader()
        writer.writerows(existing_data)
    
    print(f"  {name}: 저장 완료")


def save_daily_csv(members: List[Dict], run_date: date, crawl_time: datetime):
    """일별 CSV 파일 저장 (연월별 폴더)"""
    filename = daily_snapshot_path(run_date)
    filename.parent.mkdir(parents=True, exist_ok=True)
    crawl_time_str = crawl_time.strftime("%Y-%m-%d %H:%M:%S")

    with filename.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "오늘걸음수", "월간누적", "크롤링일시"])
        
        for m in members:
            daily = m["daily_steps"] if m["daily_steps"] is not None else ""
            writer.writerow([
                m["rank"],
                m["name"],
                daily,
                m["monthly_total"],
                crawl_time_str
            ])
    
    print(f"일별 CSV 저장: {filename}")


def save_latest(members: List[Dict], crawl_time: datetime):
    """최신 데이터 저장"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    crawl_time_str = crawl_time.strftime("%Y-%m-%d %H:%M:%S")

    filename = DATA_DIR / "latest.csv"
    with filename.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "오늘걸음수", "월누적", "크롤링일시"])
        
        for m in members:
            daily = m["daily_steps"] if m["daily_steps"] is not None else ""
            writer.writerow([
                m["rank"],
                m["name"],
                daily,
                m["monthly_total"],
                crawl_time_str
            ])
    
    print("latest.csv 저장 완료")


def load_season_config() -> Optional[Dict]:
    """config.json에서 시즌 설정을 읽고 날짜를 검증."""
    config_path = DATA_DIR / "config.json"
    if not config_path.exists():
        return None

    try:
        with config_path.open("r", encoding="utf-8-sig") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"시즌 설정 파일 로드 실패 ({config_path}): {error}") from error

    season = config.get("season")
    if not isinstance(season, dict):
        return None

    name = str(season.get("name", "")).strip()
    start_text = str(season.get("startDate", "")).strip()
    end_text = str(season.get("endDate", "")).strip()

    if not any((name, start_text, end_text)):
        return None
    if not all((name, start_text, end_text)):
        raise ValueError("시즌명, 시작일, 종료일을 모두 설정해야 합니다.")

    try:
        start_date = date.fromisoformat(start_text)
        end_date = date.fromisoformat(end_text)
    except ValueError as error:
        raise ValueError("시즌 날짜는 YYYY-MM-DD 형식이어야 합니다.") from error

    if end_date < start_date:
        raise ValueError("시즌 종료일은 시작일보다 빠를 수 없습니다.")

    return {
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
    }


def iter_dates(start_date: date, end_date: date):
    """양 끝 날짜를 포함해 하루씩 반환."""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def load_daily_steps(snapshot_date: date):
    """일별 스냅샷에서 멤버별 당일 걸음수 로드."""
    filename = daily_snapshot_path(snapshot_date)
    if not filename.exists():
        return None

    steps = {}
    unknown_names = []
    try:
        with filename.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = str(row.get("이름", row.get("성명", ""))).strip()
                if not name:
                    continue

                raw_steps = row.get(
                    "오늘걸음수",
                    row.get("오늘 걸음수", row.get("일일걸음수", row.get("일일 걸음수", ""))),
                )
                daily_steps = parse_optional_integer(raw_steps)
                if daily_steps is None or daily_steps < 0:
                    unknown_names.append(name)
                    continue
                steps[name] = daily_steps
    except (OSError, ValueError, csv.Error) as error:
        raise RuntimeError(f"일별 시즌 데이터 로드 실패 ({filename}): {error}") from error

    return steps, unknown_names


def write_season_files(rows: List[Dict], status_data: Dict, crawl_time: datetime):
    """시즌 누적 CSV와 상태 JSON 저장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    crawl_time_str = crawl_time.strftime("%Y-%m-%d %H:%M:%S")

    csv_path = DATA_DIR / "season.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "순위",
            "이름",
            "시즌누적",
            "하루평균",
            "집계일수",
            "시즌명",
            "시작일",
            "종료일",
            "집계기준일",
            "생성일시",
        ])
        for row in rows:
            writer.writerow([
                row["rank"],
                row["name"],
                row["season_total"],
                row["daily_average"],
                row["elapsed_days"],
                status_data.get("seasonName", ""),
                status_data.get("startDate", ""),
                status_data.get("endDate", ""),
                status_data.get("asOfDate") or "",
                crawl_time_str,
            ])

    status_path = DATA_DIR / "season_status.json"
    with status_path.open("w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"시즌 CSV 저장: {csv_path}")
    print(f"시즌 상태 저장: {status_path}")


def rebuild_season_summary(
    run_date: date,
    crawl_time: datetime,
    current_member_names: List[str],
):
    """설정된 시즌 기간의 일별 CSV를 다시 합산해 시즌 집계를 생성."""
    generated_at = crawl_time.isoformat()

    try:
        season = load_season_config()
    except (RuntimeError, ValueError) as error:
        status_data = {
            "version": 1,
            "status": "invalid",
            "message": str(error),
            "seasonName": "",
            "startDate": "",
            "endDate": "",
            "asOfDate": None,
            "expectedDays": 0,
            "availableDays": 0,
            "missingDates": [],
            "unknownValueCount": 0,
            "unknownDates": [],
            "complete": False,
            "generatedAt": generated_at,
        }
        write_season_files([], status_data, crawl_time)
        print(f"⚠️ 시즌 설정 오류: {error}")
        return

    if season is None:
        status_data = {
            "version": 1,
            "status": "not_configured",
            "message": "시즌이 설정되지 않았습니다.",
            "seasonName": "",
            "startDate": "",
            "endDate": "",
            "asOfDate": None,
            "expectedDays": 0,
            "availableDays": 0,
            "missingDates": [],
            "unknownValueCount": 0,
            "unknownDates": [],
            "complete": True,
            "generatedAt": generated_at,
        }
        write_season_files([], status_data, crawl_time)
        print("시즌 설정 없음: 월간 집계만 유지합니다.")
        return

    start_date = season["start_date"]
    end_date = season["end_date"]
    totals = {name: 0 for name in current_member_names if name}
    missing_dates = []
    unknown_dates = set()
    unknown_value_count = 0
    available_days = 0

    if run_date < start_date:
        season_status = "before"
        effective_date = None
        expected_dates = []
    else:
        effective_date = min(run_date, end_date)
        season_status = "active" if run_date <= end_date else "ended"
        expected_dates = list(iter_dates(start_date, effective_date))

    for snapshot_date in expected_dates:
        daily_result = load_daily_steps(snapshot_date)
        if daily_result is None:
            missing_dates.append(snapshot_date.isoformat())
            continue

        available_days += 1
        daily_steps, unknown_names = daily_result
        if unknown_names:
            unknown_dates.add(snapshot_date.isoformat())
            unknown_value_count += len(unknown_names)

        for name, steps in daily_steps.items():
            totals[name] = totals.get(name, 0) + steps

    elapsed_days = len(expected_dates)
    sorted_totals = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    rows = [
        {
            "rank": rank,
            "name": name,
            "season_total": total,
            "daily_average": total // elapsed_days if elapsed_days else 0,
            "elapsed_days": elapsed_days,
        }
        for rank, (name, total) in enumerate(sorted_totals, 1)
    ]

    complete = not missing_dates and unknown_value_count == 0
    status_data = {
        "version": 1,
        "status": season_status,
        "message": "" if complete else "일부 날짜 또는 걸음수 데이터가 누락되었습니다.",
        "seasonName": season["name"],
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "asOfDate": effective_date.isoformat() if effective_date else None,
        "expectedDays": elapsed_days,
        "availableDays": available_days,
        "missingDates": missing_dates,
        "unknownValueCount": unknown_value_count,
        "unknownDates": sorted(unknown_dates),
        "complete": complete,
        "generatedAt": generated_at,
    }
    write_season_files(rows, status_data, crawl_time)

    print(
        f"시즌 집계 완료: {season['name']} / 상태={season_status} / "
        f"집계일={available_days}/{elapsed_days} / 인원={len(rows)}명"
    )


def calculate_daily_steps(
    today_data: List[Dict],
    previous_day_data: Dict[str, int],
    run_date: date,
) -> List[Dict]:
    """전날 월 누적과 오늘 월 누적의 차이로 당일 걸음수 계산."""
    result = []
    
    for member in today_data:
        name = member["name"]
        today_total = member["steps"]
        previous_total = previous_day_data.get(name)

        # 일별 걸음수 계산
        if run_date.day == 1:
            # 월 누적이 초기화되는 매월 1일
            daily_steps = today_total
        elif previous_total is None:
            # 전날 자료가 없으면 월 누적만으로 당일 걸음수를 알 수 없다.
            daily_steps = None
        elif today_total < previous_total:
            # 같은 달에 누적값이 감소하면 API/사용자 데이터가 수정된 경우다.
            print(
                f"  경고: {name}의 월 누적 감소 "
                f"({previous_total:,} → {today_total:,}); 오늘걸음수=N/A"
            )
            daily_steps = None
        else:
            daily_steps = today_total - previous_total
        
        result.append({
            "rank": member["rank"],
            "name": name,
            "daily_steps": daily_steps,
            "monthly_total": today_total
        })
    
    return result


def print_summary(members: List[Dict], run_date: date):
    """결과 요약 출력"""
    with_daily = [m for m in members if m["daily_steps"] is not None]
    sorted_daily = sorted(with_daily, key=lambda x: -x["daily_steps"])
    
    today = run_date.isoformat()
    
    print("\n" + "=" * 55)
    print(f"📊 {today} 일별 걸음수 TOP 10")
    print("=" * 55)
    
    for i, m in enumerate(sorted_daily[:10], 1):
        print(f"  {i:2}. {m['name']:<12} 오늘: {m['daily_steps']:>7,}걸음  (누적: {m['monthly_total']:>8,})")
    
    print("=" * 55)
    
    if with_daily:
        total = sum(m["daily_steps"] for m in with_daily)
        avg = total // len(with_daily)
        print(f"📈 오늘 총 걸음수: {total:,}")
        print(f"📈 평균 걸음수: {avg:,}")
        print(f"📈 집계 인원: {len(with_daily)}명")
    print("=" * 55)


def main():
    now = get_kst_now()
    run_date = now.date()
    
    print("=" * 55)
    print("🚶 Pacer 만보걷기 일별 크롤러")
    print(f"⏰ 실행 시간 (KST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)
    
    # 0. 기존 파일 정리 (최초 1회만 실행됨)
    migrate_old_daily_files()
    
    # 1. 크롤링: 일부 페이지만 수집된 경우 저장하지 않고 실패 처리한다.
    try:
        today_data = crawl_pacer_data()
    except RuntimeError as error:
        print(f"❌ {error}")
        raise SystemExit(1) from error

    if not today_data:
        print("❌ 크롤링 실패")
        raise SystemExit(1)

    # 2. 전날 스냅샷 로드 (latest.csv는 화면 표시용으로만 사용)
    try:
        previous_day_data = load_previous_day_totals(run_date)
    except RuntimeError as error:
        print(f"❌ {error}")
        raise SystemExit(1) from error

    # 3. 일별 걸음수 계산
    daily_data = calculate_daily_steps(today_data, previous_day_data, run_date)

    # 4. 개인별 파일 업데이트 (KST 기준)
    print("\n개인별 파일 업데이트:")
    for m in daily_data:
        update_member_file(
            name=m["name"],
            run_date=run_date,
            daily_steps=m["daily_steps"],
            monthly_total=m["monthly_total"]
        )

    # 5. 일별 CSV 저장
    save_daily_csv(daily_data, run_date, now)

    # 6. latest.csv 저장
    save_latest(daily_data, now)

    # 7. 설정된 시즌 범위를 기존 일별 CSV로 재합산
    rebuild_season_summary(
        run_date=run_date,
        crawl_time=now,
        current_member_names=[m["name"] for m in daily_data],
    )

    # 8. 요약 출력
    print_summary(daily_data, run_date)


if __name__ == "__main__":
    main()
