# 🚶 Pacer 만보걷기 자동 크롤러

캔탑스 동아리 만보걷기 데이터를 **매월 말일 23:55(KST)**에 자동으로 수집합니다.

## 📁 폴더 구조

```
pacer-crawler/
├── .github/workflows/
│   └── crawl.yml        # GitHub Actions 설정
├── data/
│   ├── latest.csv       # 최신 데이터 (항상 덮어씀)
│   └── pacer_전체_YYYYMMDD_HHMMSS.csv  # 월별 데이터
├── crawler.py           # 크롤링 스크립트
└── README.md
```

## 🔧 설정 방법

### 프리셋 설정 (특정 인원만 조회)

`crawler.py` 파일에서 PRESETS 수정:

```python
PRESETS = {
    "전체": [],
    "기술표준팀": ["홍길동", "김철수", "이영희"],
    "영업팀": ["박지민", "최수현"],
}

ACTIVE_PRESET = "기술표준팀"  # 사용할 프리셋
```

## ▶️ 수동 실행 방법

1. GitHub 저장소 → **Actions** 탭
2. **Pacer 만보걷기 자동 크롤링** 클릭
3. **Run workflow** → **Run workflow** 클릭

## 📊 결과 확인

- `data/latest.csv` - 가장 최근 데이터
- `data/pacer_*.csv` - 월별 히스토리

## ⏰ 자동 실행 일정

- **매월 말일 23:55 (KST)**
- GitHub Actions가 자동 실행
- PC가 꺼져있어도 작동!
