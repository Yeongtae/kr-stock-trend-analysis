# 지수 구성종목 주간 동향 히스토리

매주 KOSPI200·KOSDAQ150 구성종목을 기준으로 다음 결과를 누적한다.

- 주간 수익률: 해당 주 첫 거래일 시가 대비 마지막 거래일 종가
- 최근 2거래일 수익률: 최근 2거래일 첫 거래일 시가 대비 마지막 거래일 종가
- 하락률 TOP15
- 외국인·기관 순매수/순매도 TOP15
- 수급 차트: 순매수 거래대금 기준, 단위 억원
- 당일 수급: KRX 기준 전체 구성종목의 실제 최근 거래일 구간 조회
- 최근 5거래일 수급: KRX 기준 정확한 최근 5거래일 구간 조회
- 최근 2·3거래일 수급: KRX 기준 정확한 거래일 구간 조회
- 다음증권은 종목 제한 가능성이 있어 수급 원천에서 사용하지 않는다.
- 60일선 후보: 돌파·강한 돌파 추세·눌림목 단계별 저장
- 당일 수급 기준과 최근 3거래일 수급 기준 강한 돌파 후보를 별도 생성·저장

## 실행

번들 Python을 사용해 실행한다.

```powershell
& 'C:\Users\hwak1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\scripts\weekly_pipeline.py --end-date 2026-09-18

# 60일선 후보 및 강한 돌파 추세 분석
& 'C:\Users\hwak1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\scripts\analyze_ma60.py --run-id 2026-09-18
```

`--end-date`를 생략하면 현재 날짜를 기준으로 실행한다. 동일한 종료일로 다시 실행하면 해당 날짜의 결과를 교체하므로 중복이 생기지 않는다.

## 다른 PC와 공유하기

이 프로젝트는 Git 원격 저장소를 기준으로 공유한다. GitHub·GitLab·Bitbucket·사내 Git 서버 중 하나에 빈 저장소를 만든 뒤, 최초 한 번만 아래를 실행한다.

```powershell
git remote add origin <원격 저장소 URL>
git branch -M main
git add -A
git commit -m "chore: initialize shared stock analysis workspace"
git push -u origin main
```

다른 PC에서는 저장소를 복제하고 환경 확인 스크립트를 실행한다.

```powershell
git clone <원격 저장소 URL>
cd <복제된 저장소 폴더>
.\scripts\setup_shared.ps1
```

이후 작업 순서는 항상 `pull → 작업/실행 → commit → push`로 맞춘다.

```powershell
git pull --rebase
.\scripts\setup_shared.ps1
.\scripts\run_pipeline.ps1 -EndDate 2026-09-18
git add -A
git commit -m "data: update 2026-09-18 stock analysis"
git push
```

현재 이력 재현을 위해 `stock_history.db`, `raw/`, `reports/`, `exports/`는 공유 대상에 포함한다. SQLite는 단일 작성 방식이므로 두 PC에서 동시에 파이프라인을 실행하지 말고, 실행 전 반드시 `git pull --rebase`를 한다. 동시에 수정하는 팀 작업이 필요해지면 DB만 PostgreSQL 등 중앙 DB로 분리하는 것이 다음 단계다.

## 보관 구조

- `stock_history.db`: 누적 조회용 SQLite
- `raw/YYYY-MM-DD/`: 구성종목·가격·KRX 수급 원천 JSON
- `reports/YYYY-MM-DD/`: 사람이 보는 HTML 리포트와 필요한 CSV
- `exports/YYYY-MM-DD/`: CSV 내보내기 파일
- `schema.sql`: 데이터베이스 구조

수급 원천은 `raw/YYYY-MM-DD/investor_flow_sources.json`에서 확인할 수 있다. `reports/YYYY-MM-DD/report-pairs.html`에서 당일 수급+추천종목과 최근 3거래일 수급+후보+추천종목의 묶음을 볼 수 있다. 최근 3거래일 수급은 `recent3-summary.html`, 최근 5거래일 수급은 `five-day-summary.html`이다. HTML은 당일·최근 3일·최근 5일 수급, 60일선 후보, 당일/최근 3일 추천종목만 유지하며, 강한 돌파 상세 목록은 `ma60_strong_breakouts*.csv`로 저장한다. 후보 이력은 `ma60_candidate_sets` 테이블에 기간별로 저장한다.

금액은 DB와 CSV에 원 단위 정수로 저장하고, 리포트에서만 억원으로 표시한다. 구성종목 스냅샷도 매 실행마다 저장해 리밸런싱 이후 과거 결과를 재현할 수 있게 한다.
