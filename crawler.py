"""
Pacer 만보걷기 크롤러
매일 23:59 KST에 자동 실행
개인별 월간 파일로 저장
"""

import requests
import csv
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict

# 설정
GROUP_ID = 31844011
BASE_URL = "https://www.mypacer.com/api/v1/leaderboard"
REFERER = "https://www.mypacer.com/clubs/1n3qqmrn/-ju-kaentabseu-suwon-gyeonggi-do"

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


def get_kst_now():
    """한국 시간 반환"""
    return datetime.now(KST)


def crawl_pacer_data() -> List[Dict]:
    """Pacer API에서 전체 멤버 데이터 크롤링"""
    all_members = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": REFERER
    }
    
    print("크롤링 시작...")
    
    for anchor in range(0, 140, 10):
        url = f"{BASE_URL}/{GROUP_ID}?anchor={anchor}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data.get("success"):
                break
                
            rank_list = data.get("data", {}).get("rank_list", [])
            if not rank_list:
                break
            
            for item in rank_list:
                all_members.append({
                    "rank": int(item.get("rank", 0)),
                    "name": item.get("display_text", {}).get("main", ""),
                    "steps": int(float(item.get("display_score_text", 0)))
                })
            
            print(f"  anchor={anchor}: {len(rank_list)}명 수집")
            
            if not data.get("data", {}).get("paging", {}).get("has_more"):
                break
                
        except Exception as e:
            print(f"  오류 발생 (anchor={anchor}): {e}")
            break
    
    print(f"크롤링 완료: 총 {len(all_members)}명")
    return all_members


def load_yesterday_total() -> Dict[str, int]:
    """어제 월누적 데이터 로드 (latest.csv에서)"""
    if not os.path.exists("data/latest.csv"):
        return {}
    
    data = {}
    try:
        with open("data/latest.csv", "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                data[row["이름"]] = int(row["월누적"])
    except Exception as e:
        print(f"어제 데이터 로드 실패: {e}")
        return {}
    
    return data


def safe_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자 제거"""
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name


def update_member_file(name: str, date_str: str, daily_steps: int, monthly_total: int):
    """개인별 월간 파일 업데이트"""
    os.makedirs("data/members", exist_ok=True)
    
    # 파일명: 홍길동_2026년2월_Data.csv (KST 기준)
    now = get_kst_now()
    month_str = f"{now.year}년{now.month}월"
    safe_name = safe_filename(name)
    filename = f"data/members/{safe_name}_{month_str}_Data.csv"
    
    # 기존 데이터 로드
    existing_data = []
    existing_dates = set()
    
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8-sig") as f:
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
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["날짜", "오늘걸음수", "월누적"])
        writer.writeheader()
        writer.writerows(existing_data)
    
    print(f"  {name}: 저장 완료")


def save_daily_csv(members: List[Dict], date_str: str):
    """일별 CSV 파일 저장 (연월별 폴더)"""
    now = get_kst_now()
    
    # 폴더: data/daily/2026년2월/
    month_folder = f"data/daily/{now.year}년{now.month}월"
    os.makedirs(month_folder, exist_ok=True)
    
    filename = f"{month_folder}/{now.strftime('%Y-%m-%d')}.csv"
    crawl_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "오늘걸음수", "월간누적", "크롤링일시"])
        
        for m in members:
            daily = m["daily_steps"] if m["daily_steps"] is not None else ""
            writer.writerow([
                m["rank"],
                m["name"],
                daily,
                m["monthly_total"],
                crawl_time
            ])
    
    print(f"일별 CSV 저장: {filename}")


def save_latest(members: List[Dict]):
    """최신 데이터 저장"""
    os.makedirs("data", exist_ok=True)
    
    crawl_time = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open("data/latest.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "오늘걸음수", "월누적", "크롤링일시"])
        
        for m in members:
            daily = m["daily_steps"] if m["daily_steps"] is not None else ""
            writer.writerow([
                m["rank"],
                m["name"],
                daily,
                m["monthly_total"],
                crawl_time
            ])
    
    print("latest.csv 저장 완료")


def calculate_daily_steps(today_data: List[Dict], yesterday_data: Dict[str, int]) -> List[Dict]:
    """일별 걸음수 계산"""
    result = []
    
    for member in today_data:
        name = member["name"]
        today_total = member["steps"]
        yesterday_total = yesterday_data.get(name, 0)
        
        # 일별 걸음수 계산
        if yesterday_total == 0:
            # 어제 데이터 없음 (신규 또는 월초)
            # 월누적이 곧 오늘 걸음수일 가능성 높음
            daily_steps = today_total
        elif today_total < yesterday_total:
            # 월초 리셋됨
            daily_steps = today_total
        else:
            daily_steps = today_total - yesterday_total
        
        result.append({
            "rank": member["rank"],
            "name": name,
            "daily_steps": daily_steps,
            "monthly_total": today_total
        })
    
    return result


def print_summary(members: List[Dict]):
    """결과 요약 출력"""
    with_daily = [m for m in members if m["daily_steps"] is not None]
    sorted_daily = sorted(with_daily, key=lambda x: -x["daily_steps"])
    
    today = get_kst_now().strftime("%Y-%m-%d")
    
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
    
    print("=" * 55)
    print("🚶 Pacer 만보걷기 일별 크롤러")
    print(f"⏰ 실행 시간 (KST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)
    
    # 1. 크롤링
    today_data = crawl_pacer_data()
    if not today_data:
        print("❌ 크롤링 실패")
        return
    
    # 2. 어제 데이터 로드
    yesterday_data = load_yesterday_total()
    
    # 3. 일별 걸음수 계산
    daily_data = calculate_daily_steps(today_data, yesterday_data)
    
    # 4. 개인별 파일 업데이트 (KST 기준)
    today_str = now.strftime("%m/%d")
    print("\n개인별 파일 업데이트:")
    for m in daily_data:
        update_member_file(
            name=m["name"],
            date_str=today_str,
            daily_steps=m["daily_steps"],
            monthly_total=m["monthly_total"]
        )
    
    # 5. 일별 CSV 저장
    save_daily_csv(daily_data, today_str)
    
    # 6. latest.csv 저장
    save_latest(daily_data)
    
    # 7. 요약 출력
    print_summary(daily_data)


if __name__ == "__main__":
    main()
