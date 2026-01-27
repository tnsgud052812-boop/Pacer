"""
Pacer 만보걷기 크롤러
매월 마지막 날 23:55에 자동 실행
"""

import requests
import json
import csv
import os
from datetime import datetime
from typing import List, Dict

# 설정
GROUP_ID = 31844011
BASE_URL = "https://www.mypacer.com/api/v1/leaderboard"
REFERER = "https://www.mypacer.com/clubs/1n3qqmrn/-ju-kaentabseu-suwon-gyeonggi-do"

# 프리셋 설정 (원하는 인원만 조회하려면 여기에 이름 추가)
PRESETS = {
    "전체": [],  # 빈 리스트 = 전체 조회
    # "기술표준팀": ["홍길동", "김철수", "이영희"],  # 예시
}

# 기본 프리셋 선택
ACTIVE_PRESET = "전체"


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


def filter_members(members: List[Dict], preset_name: str) -> List[Dict]:
    """프리셋에 따라 멤버 필터링"""
    filter_names = PRESETS.get(preset_name, [])
    
    if not filter_names:
        return members
    
    filter_set = set(name.lower() for name in filter_names)
    
    filtered = []
    for m in members:
        member_name = m["name"].lower()
        for name in filter_set:
            if member_name == name or name in member_name or member_name in name:
                filtered.append(m)
                break
    
    return sorted(filtered, key=lambda x: x["rank"])


def save_to_csv(members: List[Dict], preset_name: str) -> str:
    """CSV 파일로 저장"""
    # data 폴더 생성
    os.makedirs("data", exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 파일명에 프리셋 이름 포함
    safe_preset = preset_name.replace("/", "_").replace("\\", "_")
    filename = f"data/pacer_{safe_preset}_{timestamp}.csv"
    
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "월간걸음수", "크롤링일시", "프리셋"])
        
        for m in members:
            writer.writerow([
                m["rank"],
                m["name"],
                m["steps"],
                crawl_time,
                preset_name
            ])
    
    print(f"저장 완료: {filename}")
    return filename


def save_latest(members: List[Dict], preset_name: str) -> str:
    """최신 데이터를 고정 파일명으로도 저장 (쉽게 접근용)"""
    os.makedirs("data", exist_ok=True)
    
    crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filename = "data/latest.csv"
    
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["순위", "이름", "월간걸음수", "크롤링일시", "프리셋"])
        
        for m in members:
            writer.writerow([
                m["rank"],
                m["name"],
                m["steps"],
                crawl_time,
                preset_name
            ])
    
    print(f"최신 데이터 저장: {filename}")
    return filename


def main():
    print("=" * 50)
    print("Pacer 만보걷기 자동 크롤러")
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"프리셋: {ACTIVE_PRESET}")
    print("=" * 50)
    
    # 크롤링
    all_members = crawl_pacer_data()
    
    if not all_members:
        print("크롤링 실패: 데이터 없음")
        return
    
    # 필터링
    filtered = filter_members(all_members, ACTIVE_PRESET)
    print(f"필터링 결과: {len(filtered)}명")
    
    # 저장
    save_to_csv(filtered, ACTIVE_PRESET)
    save_latest(filtered, ACTIVE_PRESET)
    
    # 요약 출력
    total_steps = sum(m["steps"] for m in filtered)
    print("\n" + "=" * 50)
    print(f"총 인원: {len(filtered)}명")
    print(f"총 걸음수: {total_steps:,}")
    print(f"평균 걸음수: {total_steps // len(filtered):,}" if filtered else "")
    print("\n[상위 10명]")
    for m in filtered[:10]:
        print(f"  {m['rank']:3}위. {m['name']:<12} {m['steps']:>10,} 걸음")
    print("=" * 50)


if __name__ == "__main__":
    main()
