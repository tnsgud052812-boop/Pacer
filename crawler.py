"""
Pacer 만보걷기 크롤러 (일별 버전)
매일 23:58 KST에 자동 실행
전날 대비 걸음수 변화 계산
"""

import requests
import csv
import os
from datetime import datetime, timedelta
from typing import List, Dict

# 설정
GROUP_ID = 31844011
BASE_URL = "https://www.mypacer.com/api/v1/leaderboard"
REFERER = "https://www.mypacer.com/clubs/1n3qqmrn/-ju-kaentabseu-suwon-gyeonggi-do"


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


def load_yesterday_data() -> Dict[str, int]:
    """어제 데이터 로드"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    filename = f"data/daily/{yesterday}.csv"
    
    if not os.path.exists(filename):
        print(f"어제 데이터 없음: {filename}")
        return {}
    
    data = {}
    with open(filename, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["이름"]] = int(row["월간누적"])
    
    print(f"어제 데이터 로드: {len(data)}명")
    return data


def calculate_daily_steps(today_data: List[Dict], yesterday_data: Dict[str, int]) -> List[Dict]:
    """일별 걸음수 계산"""
    result = []
    
    for member in today_data:
        name = member["name"]
        today_total = member["steps"]
        yesterday_total = yesterday_data.get(name, 0)
        
        # 일별 걸음수 계산
        if yesterday_total == 0:
            # 어제 데이터 없음 (신규 또는 첫 집계)
            daily_steps = None
        elif today_total < yesterday_total:
            # 월초 리셋됨 - 오늘 누적이 곧 오늘 걸음수
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


def save_daily_data(members: List[Dict]) -> str:
    """일별 데이터 저장"""
    os.makedirs("data/daily", exist_ok=True)
    
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"data/daily/{today}.csv"
    crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
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
    
    print(f"저장 완료: {filename}")
    return filename


def save_latest(members: List[Dict]) -> str:
    """최신 데이터 저장"""
    os.makedirs("data", exist_ok=True)
    
    crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open("data/latest.csv", "w", newline="", encoding="utf-8-sig") as f:
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
    
    return "data/latest.csv"


def print_summary(members: List[Dict]):
    """결과 요약 출력"""
    # 오늘 걸음수 기준 정렬
    with_daily = [m for m in members if m["daily_steps"] is not None]
    sorted_daily = sorted(with_daily, key=lambda x: -x["daily_steps"])
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    print("\n" + "=" * 55)
    print(f"📊 {today} 일별 걸음수 TOP 10")
    print("=" * 55)
    
    for i, m in enumerate(sorted_daily[:10], 1):
        print(f"  {i:2}. {m['name']:<12} 오늘: {m['daily_steps']:>7,}걸음  (누적: {m['monthly_total']:>8,})")
    
    print("=" * 55)
    
    # 통계
    if with_daily:
        total = sum(m["daily_steps"] for m in with_daily)
        avg = total // len(with_daily)
        print(f"📈 오늘 총 걸음수: {total:,}")
        print(f"📈 평균 걸음수: {avg:,}")
        print(f"📈 집계 인원: {len(with_daily)}명")
    print("=" * 55)


def main():
    print("=" * 55)
    print("🚶 Pacer 만보걷기 일별 크롤러")
    print(f"⏰ 실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)
    
    # 1. 크롤링
    today_data = crawl_pacer_data()
    if not today_data:
        print("❌ 크롤링 실패")
        return
    
    # 2. 어제 데이터 로드
    yesterday_data = load_yesterday_data()
    
    # 3. 일별 걸음수 계산
    daily_data = calculate_daily_steps(today_data, yesterday_data)
    
    # 4. 저장
    save_daily_data(daily_data)
    save_latest(daily_data)
    
    # 5. 요약 출력
    print_summary(daily_data)


if __name__ == "__main__":
    main()
