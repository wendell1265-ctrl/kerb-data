# kerb-data

KERB 앱이 내려받는 원격 트랙 데이터(정적 파일, GitHub Pages로 제공).
이 저장소는 직접 고치지 않는다 — 프로젝트의 `tools/publish-data.sh`가 만들고 푸시한다.

## 결승 결과 보완 (자동)

`.github/workflows/race-results.yml` 이 레이스 주(일·월·화, UTC)에 30분마다 `tools/fetch-results.py` 를 실행한다.
Jolpica에 그 라운드 결승 결과가 없으면 FIA 문서 목록에서 Final(없으면 Provisional) Race Classification PDF를 찾아
`results/{시즌}-{라운드}.json` 을 만들고 manifest에 등록한다. 앱은 Jolpica를 우선하고, 없을 때만 이 파일을 쓴다.
검증(드라이버 수·차번 매칭·순위 중복)에 실패하면 아무것도 올리지 않고 issue를 만든다.
