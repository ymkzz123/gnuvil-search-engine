# 그누빌 검색엔진

시작 페이지에서 링크를 따라 HTML 문서를 수집하고, 제목·본문을 SQLite에 색인한 뒤 TF-IDF 관련도순으로 검색합니다. Python 3.10 이상에서 표준 라이브러리만으로 실행할 수 있습니다.

## 샘플 페이지 6개로 실행하기

저장소 루트에서 터미널 두 개를 엽니다. 첫 번째 터미널에서 스터디용 HTML을 제공합니다.

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory examples/site
```

두 번째 터미널에서 목록 페이지를 수집하고 검색합니다.

```sh
python main.py crawl http://127.0.0.1:8765/index.html --output data/documents.json
python main.py search "메모리"
python main.py search "검색엔진 역색인"
python main.py search "검색엔진 메모리" --mode OR
```

수집 결과는 `indexed: 6`입니다. `메모리`의 첫 결과는 **가상 메모리**, 두 번째는 **CPU 캐시**입니다. `검색엔진 역색인`은 두 단어가 모두 있는 **검색엔진 구조** 한 건을 반환합니다. 색인은 기본적으로 `data/search.sqlite3`에 저장되므로 수집 서버를 종료한 뒤에도 검색할 수 있습니다.

다른 팀원은 내보낸 JSON을 받아 별도 DB에 같은 색인을 만들 수 있습니다. JSON은 `id`, `url`, `title`, `body`를 담은 문서 배열입니다. 가져올 때는 URL로 같은 문서를 식별하고 대상 DB의 ID를 사용합니다.

```sh
python main.py --db data/copy.sqlite3 index data/documents.json
python main.py --db data/copy.sqlite3 search "역색인"
```

## JSON API

두 번째 터미널에서 API를 시작합니다. API는 기본적으로 `127.0.0.1:8000`에서 실행됩니다. `--site`에는 수집할 출처를 등록하며 여러 번 지정할 수 있습니다. 검색만 할 때는 생략할 수 있습니다.

```sh
python main.py serve --site http://127.0.0.1:8765
```

| 요청 | 입력 | 응답 |
| --- | --- | --- |
| `POST /crawl` | JSON 본문의 `url`, 선택 항목 `max_pages`(기본 10, 1~100), `max_depth`(기본 2, 0~10) | 수집·색인을 마치면 처리한 문서 수 `indexed`와 실패 목록 `errors` 반환 |
| `GET /search` | 필수 `q`, 선택 항목 `mode`(기본 AND 또는 OR), `limit`(기본 10, 1~100), `offset`(기본 0) | `query`, `mode`, 전체 일치 건수 `total`, `limit`, `offset`, 결과 배열 `results` |

PowerShell에서 두 경로를 호출할 수 있습니다.

```powershell
$body = '{"url":"http://127.0.0.1:8765/index.html","max_pages":10}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/crawl' -ContentType 'application/json' -Body $body
$query = [uri]::EscapeDataString('메모리')
$result = Invoke-RestMethod "http://127.0.0.1:8000/search?q=$query&limit=5"
$result.results | Format-Table title, url, score
```

각 검색 결과는 `id`, `title`, `url`, 본문 미리보기 `snippet`, 관련도 `score`를 담습니다. 일치하는 문서가 없으면 HTTP 200과 빈 배열을 반환합니다. 잘못된 입력은 400, 등록하지 않은 수집 출처는 403, 다른 API 수집이 진행 중이면 409를 반환합니다. 수집 중 개별 페이지에 실패한 경우 응답의 `errors`에서 URL과 사유를 확인할 수 있습니다.

## 수집·색인·검색 방식

- **crawler/**는 큐로 같은 출처(스킴·호스트·포트)의 링크를 너비 우선 탐색합니다. URL의 `#fragment`를 제거해 중복 방문을 줄이고, robots.txt의 접근 규칙과 페이지 수·깊이 제한을 적용합니다. 시작 페이지 깊이는 0입니다. HTML 응답은 페이지당 2 MiB까지 읽으며 자바스크립트를 실행하지 않습니다.
- **indexer/**는 한글·영문·숫자를 토큰화하고 문서별 단어 빈도를 저장합니다. 일부 한국어 조사는 원문과 함께 조사를 뗀 형태도 색인합니다. 예를 들어 `검색엔진은`을 `검색엔진`으로 찾을 수 있습니다. 같은 URL을 재수집하면 ID를 유지하면서 본문과 역색인을 한 트랜잭션으로 교체합니다.
- **search/**는 AND에서 모든 검색어가 일치하는 문서, OR에서 하나 이상 일치하는 문서를 고릅니다. 검색어마다 `(등장 횟수 / 문서 토큰 수) × (1 + ln((전체 문서 수 + 1) / (일치 문서 수 + 1)))`를 더합니다. 동점이면 문서 ID가 작은 순서로 반환합니다.

## 검증

```sh
python -m unittest discover -s tests -v
```

테스트는 임시 DB와 로컬 HTTP 서버를 사용합니다. 샘플 수집, 검색어·모드 조합 9개, 관련도 순서, 페이지네이션, 재색인, API 오류 응답, CLI의 JSON 내보내기와 가져오기를 검증합니다.
