# AskProject

[![test](https://github.com/wonsss/askproject/actions/workflows/test.yml/badge.svg)](https://github.com/wonsss/askproject/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/wonsss/askproject)](https://github.com/wonsss/askproject/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![macOS 13+ · Apple silicon](https://img.shields.io/badge/macOS-13%2B%20%C2%B7%20Apple%20silicon-lightgrey)

**기획자가 슬랙에서 묻고, 코드와 기획 문서가 근거를 달아 답하는 봇.**

"이 화면 네이티브야 웹뷰야?", "푸시는 몇 시에 와?", "여기 공유 버튼 넣으면 뭐가 바뀌어?" 같은 질문에 비공개 코드베이스와 기획 문서(레포 docs·노션·슬랙 채널)를 읽고 답합니다. 모든 답변에 `파일:라인` 근거를 붙이고, 그 근거가 봇이 실제로 읽은 구간인지 코드로 검증합니다. 답은 웹 콘솔에 쌓여 팀이 다시 찾아 씁니다.

> **In English** — AskProject is a Slack bot that answers product managers' questions about a private codebase and planning docs (repo docs, Notion, Slack channels). Every answer cites `file:line` evidence that the bot verifies it actually read. It runs on your own Mac (signed & notarized app with a setup wizard) or server, uses your own Claude API key, read-only GitHub/Slack/Notion access, and has no telemetry. UI and docs are in Korean.

[⬇️ 앱 받기 (DMG)](https://github.com/wonsss/askproject/releases/latest) · [데이터와 개인정보](docs/privacy.md) · [문서](#문서) · [기여하기](CONTRIBUTING.md)

## 할 수 있는 것

| 기능 | 예시 질문 | 결과 |
| --- | --- | --- |
| 빠른 답 | 앱 하단 탭에는 어떤 메뉴들이 있어요? | 소형 모델, 약 10초, 약 $0.003 |
| 심층 탐색 | 앱 웹뷰 상세에서 찜 버튼을 누르면 어떤 흐름으로 저장돼요? | 웹 → 앱 → API까지 레이어별 근거, 15~30초, 약 $0.05 |
| 되묻기 | 알림 로직 알려줘 | 탐색 없이 도메인 버튼 제시($0.0003), 고르면 그 범위로 답 |
| 기획서·코드 불일치 | 매일 받기 푸시는 몇 시에 와요? 기획서랑 같아요? | 노션 기획서와 코드를 함께 인용하고 ⚠️ 불일치 지적 |
| 로직 흐름도 | 푸시 대기 중에 알림을 끄면 어떻게 돼요? | 조건이 2개 이상이면 근거가 달린 흐름도 HTML을 스레드에 첨부 |
| 와이어프레임 | 커뮤니티 기능 추가 시 와이어프레임 만들어줘 | 기존 화면 관례를 따른 저충실도 HTML, 약 $0.04 |
| 스크린샷 | (화면 캡처 첨부) 이 화면은 어떤 코드야? / 여기에 공유 버튼 추가하면? | 화면 문구로 코드 역추적, 변경 요청이면 변경안 목업 + 영향받는 코드 위치 |
| 후속 질문 | — | 답변 아래 💬 이어서 물어보기 버튼 2~3개 |
| 웹 콘솔 | — | 지난 답 검색, 최신성 배지(근거 코드가 그 뒤로 바뀌었는지), 운영 점검, 평가 이력 |

답변 아래 버튼: 근거 전체 보기 · 👍 · 👎 · 개발자에게 확인 요청(지정 채널에 올림). 대상 레포는 설정으로 정하므로 특정 서비스에 묶여 있지 않습니다. GitHub에서 읽을 수 있는 레포면 어디든 붙일 수 있습니다.

## 데이터와 개인정보

AskProject에는 **개발자가 운영하는 서버가 없고, 텔레메트리·분석·오류 보고도 없습니다.** 데이터는 봇을 설치한 맥(또는 서버)과 **설치한 사람이 직접 연결한 서비스** 사이에서만 오갑니다.

| 어디로 | 무엇을 |
| --- | --- |
| **Anthropic Claude API** | 질문, 관련 **코드·문서 조각**, 첨부한 스크린샷 — 답을 만들려면 꼭 필요합니다. 설치한 사람의 API 키와 [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms)를 따릅니다 |
| Slack | 답변·파일 게시, 첨부 이미지 받기, (선택) 지정 채널 읽기 |
| GitHub · Notion | 코드·문서를 **받기만** 합니다(읽기 전용 토큰) |
| 업데이트 확인 (앱) | 새 버전이 있는지 묻는 요청만. 시스템 정보는 보내지 않습니다 |

- 번들된 `claude` 실행 파일이 기본으로 보내는 사용 지표·오류 보고도 모두 껐습니다.
- 에이전트에는 읽기 전용 도구만 있어, 코드 속 지시문이 있어도 다른 곳으로 데이터를 보낼 수단이 없습니다.
- 질문·답은 감사 로그로 **이 맥에 영구 보관**되고, 비밀값은 macOS Keychain에 둡니다.

통신 지점 전체(코드 위치·끄는 법), 로컬 보관 항목, 슬랙 사용자에게 알릴 내용은 [docs/privacy.md](docs/privacy.md)에 있습니다.

## 시작하기

### 1. 맥 앱 (권장, 비개발자용)

워크스페이스 관리자 한 명이 한 대에만 설치하면 됩니다. 기획자는 슬랙에서 `@AskProject`로 묻기만 합니다.

1. [Releases](https://github.com/wonsss/askproject/releases/latest)에서 `AskProject-<버전>.dmg`를 받아 응용 프로그램 폴더로 옮기고 엽니다(Apple 공증 완료, macOS 13+, Apple silicon).
2. 설정 마법사를 따라갑니다. 터미널·git·Homebrew는 필요 없습니다.
   1. **슬랙 앱**: 설정이 채워진 생성 화면이 열립니다. 관리자 승인이 필요하면 요청 문구를 복사해 보내고, 승인되면 이어서 합니다.
   2. **GitHub**: 읽기 전용(contents: read) 토큰 생성 화면이 채워져 열립니다. 토큰을 넣고 레포를 고릅니다.
   3. **Claude API 키**: [Claude Console](https://console.anthropic.com/settings/keys)에서 만든 키를 넣습니다.
   4. **답할 채널**: 코드 내용을 보여 줘도 되는 채널만 고릅니다.
   5. **점검 → 켜기**: 실패 항목마다 '고치기' 버튼이 그 단계로 돌아갑니다.

필요한 것: 슬랙 워크스페이스(앱 설치 권한 또는 관리자 승인), GitHub 레포 읽기 권한, Claude API 키(질문당 보통 $0.01~$0.10, [비용](docs/cost.md)). 맥이 잠들면 봇도 멈추므로 설정의 '맥 운영'에서 절전·자동 로그인을 정합니다.

### 2. 서버 (Docker)

24시간 팀 운영이라면 VPS에 Docker Compose로 띄웁니다. Socket Mode라 인바운드 포트가 필요 없습니다. → [docs/deploy-vultr.md](docs/deploy-vultr.md)

### 3. 개발 (소스에서)

[uv](https://docs.astral.sh/uv/)와 [ripgrep](https://github.com/BurntSushi/ripgrep)이 필요합니다.

```bash
git clone https://github.com/wonsss/askproject && cd askproject
uv sync
cp .env.example .env                    # 슬랙·GitHub·Claude 값 채우기
# config/repos.yaml에 대상 레포 등록 (첫 명령 때 템플릿이 생긴다. git 제외 — config/README.md)
uv run askproject doctor --online       # 환경·키·크레딧·노션·DB·백업 점검
uv run askproject sync                  # 레포 받기(GitHub API) + 인덱싱 + 노션·슬랙 수집 (Claude 비용 없음)
uv run askproject serve                 # 슬랙 봇
uv run askproject console               # 웹 콘솔 http://127.0.0.1:18787 (사용 중이면 빈 포트로)
```

자주 쓰는 명령은 `make`로 봅니다(test·doctor·sync·serve·console·release 등).

## 구조

```
슬랙 (Socket Mode, 멘션·DM·스크린샷)
  └ 질의 파이프라인
     1. 스코핑   소형 모델(thinking 끔) + 도메인 맵 → 유형·범위·확신도
     2. 라우팅   거절 · 미지원 · 되묻기 · 빠른 답(소형) · 심층 탐색(대형) · 와이어프레임(대형)   ← 코드로 강제
     3. 에이전트 Claude Agent SDK, 내장 도구 끔, 읽기 전용 도구만, 범위 하드 필터(1회 확장)
        ├ find_screen · find_api · find_bridge   화면 방식, API 연결, 웹뷰↔앱 메시지
        ├ code_search · read_code · list_dir
        └ search_docs · read_doc                 레포 docs · 노션 · 슬랙 지정 채널
     4. 근거 검증 인덱스 존재 · 줄 범위 · 도구로 실제 본 구간(폴더는 list_dir) → 확신도
     5. 후처리   흐름도·화면 명세 → 결정적 렌더러(HTML·SVG), 후속 질문 → 버튼
  └ 감사 로그 (SQLite, append-only) ──→ 웹 콘솔 (읽기 전용)

동기화 (10분 주기): GitHub REST API(tarball·trees, git 불필요) → 코드 인덱스 · 구조 맵 · 문서 인덱스 · 커밋별 파일 해시
                     노션(API 토큰 연결) · 슬랙(지정 공개 채널, 최근 90일) 수집

맥 앱: SwiftUI 메뉴 막대(화면만) → 백엔드 CLI(JSON) · 내장 파이썬 · SMAppService 상시 실행 · Keychain
```

Python 3.12 · Claude Agent SDK · Anthropic SDK · Slack Bolt · Starlette · SQLite(FTS5) · SwiftUI. 소스 지도는 [CLAUDE.md](CLAUDE.md)의 "구조 요약"에 있습니다.

## 명령

```bash
uv run askproject ask "앱에서 상세 화면은 네이티브야 웹뷰야?"
uv run askproject ask "알림 로직 알려줘" --domains <도메인-id>
uv run askproject ask "커뮤니티 기능 추가 시 와이어프레임 만들어줘"   # → data/wireframes/*.html
uv run askproject eval [--ids q01]      # 골든 세트(eval/golden.yaml, 형식은 eval/golden.example.yaml) → eval/results/
uv run askproject stats
uv run askproject host on|off|status    # 맥북 호스트 모드: 봇·콘솔 상시 실행, 잠자기 방지, 매일 백업
uv run askproject backup | restore-check | maintenance | nightly   # 감사 로그 백업·복구 확인·정리
uv run askproject backups [--json] · restore <백업 파일> --yes      # 백업 목록 · 백업으로 DB 교체(docs/operations.md)
uv run askproject redact <query_id> --reason "…"   # 개인정보 유입 시 텍스트 가림
uv run askproject learn domains|glossary [--dry-run]   # AI 초안 → 검증 → 미리 보기 (docs/learn.md)
uv run askproject learn domains --apply <제안 id> [--accept a,b]   # 승인한 항목만 적용 (백업, 사람이 고친 항목은 잠금)
uv run askproject migrate               # DB 스키마·설정 형식 올리기 (백업 후, 다운그레이드 거부)
uv run askproject diagnostics           # 비밀값을 가린 진단 묶음 zip (이 맥에만 저장)
uv run pytest
```

`ask`·`eval`·`serve`·`learn`(--dry-run·--apply 제외)만 Claude 비용이 듭니다. 경로별 비용은 [docs/cost.md](docs/cost.md)에 있습니다.

## 설정

### 슬랙 앱

마법사를 쓰지 않을 때:

0. 가장 빠른 방법: api.slack.com/apps → Create New App → **From a manifest** → `slack-app-manifest.yaml` 붙여 넣기
1. **Socket Mode** 켜기 → App-Level Token(`connections:write`) → `ASKPROJECT_SLACK_APP_TOKEN`
2. Bot Token Scopes
   - 기본: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`
   - 스크린샷: `files:read` · 흐름도·와이어프레임 파일 올리기: `files:write`
   - 슬랙 채널 소스: `channels:history`, `channels:read`, `users:read`
   - 권한을 추가하면 **앱을 워크스페이스에 다시 설치**해야 적용된다
3. Event Subscriptions: `app_mention`, `message.im` · Interactivity 켜기(Request URL 불필요)
4. 설치 → Bot User OAuth Token → `ASKPROJECT_SLACK_BOT_TOKEN`
5. 답할 채널에 봇 초대 → 채널 ID(C…)를 `ASKPROJECT_ALLOWED_CHANNELS`(DM 허용은 `dm`)
6. (선택) 소스 채널 `ASKPROJECT_SLACK_SOURCE_CHANNELS` · 확인 요청 채널 `ASKPROJECT_ESCALATION_CHANNEL` · 호스트 중복 감지 채널 `ASKPROJECT_HOST_CHANNEL`

봇은 질문을 받을 때 "질문과 답은 팀 콘솔에 공개되고, 질문과 관련 코드는 Claude API로 처리된다"는 안내를 함께 보여 줍니다(DM 포함).

### 노션 (선택)

노션 화면 기준 2026-10. 예전 "내부 통합"이 지금은 **연결 → API 토큰**이다.

1. 노션 **설정 → 연결 → 개발자 도구**(app.notion.com/developers/connections) → **신규 연결**, 이름 `AskProject`, 인증 방법 **API 토큰**
2. **구성** 탭: 기능은 **콘텐츠 읽기**만 켠다 · 토큰(`ntn_…`)을 `ASKPROJECT_NOTION_TOKEN`에
3. 기획 문서 최상위 페이지 → **⋯ → 연결 → 연결 추가** → `AskProject` (하위 페이지 자동 포함)
4. 최상위 페이지 ID(주소 끝 32자리)를 `ASKPROJECT_NOTION_ROOT_PAGE_ID`에 → `doctor --online` → `sync`

### 파일과 환경 변수

| 파일 / 환경변수 | 내용 |
| --- | --- |
| `config/repos.yaml` | 대상 레포, 기준 ref, 제외 규칙, 레포별 `structure:` 구조 맵 규칙 |
| `config/domains.yaml` | 기획자 관점 도메인 목록과 도메인별 허용 경로. 직접 쓰거나 `askproject learn domains`(앱: 설정 → 도메인·용어 → 학습하기)로 초안을 받아 항목별로 적용 |
| `config/glossary.yaml` | 기획 용어 → 코드 식별자 힌트. `askproject learn glossary`로도 채운다 |
| `config/*.yaml` 공통 | 서비스 정보가 들어가 **git에 넣지 않는다**(.gitignore). 템플릿은 `src/askproject/defaults/`, 없으면 실행할 때 복사 |
| `eval/golden.yaml` | 골든 세트 (정답·근거·기대 행동. 형식은 `eval/golden.example.yaml`, git 제외) |
| `ASKPROJECT_MODEL_LARGE` / `_SMALL` | 기본 `claude-sonnet-5-5` / `claude-haiku-5-5` |
| `ASKPROJECT_AGENT_EFFORT_LARGE` / `_SMALL` | 비우면 모델 기본값 |
| `ASKPROJECT_AGENT_MAX_BUDGET_USD` | 질문 하나의 비용 상한 (기본 0.5) |
| `ASKPROJECT_ALLOWED_CHANNELS` | 봇이 답할 채널 ID(쉼표 구분, DM은 `dm`). 비우면 봇이 초대된 모든 채널 |
| `ASKPROJECT_BACKUP_DIR` · `_KEEP_DAYS` · `_UPLOAD_CMD` | 백업 위치, 로컬 보관 일수, 서버 밖 복사 명령(`{file}`) |
| `ASKPROJECT_BACKUP_COPY_DIR` · `_COPY_KEEP_DAYS` | 백업 사본 폴더(iCloud Drive 등 동기화 폴더, 앱 설정 → 맥 운영), 그 폴더의 보관 일수(기본 90) |
| `ASKPROJECT_LLM_PROVIDER` | `api`(기본). `subscription`(로컬 `claude` CLI 구독 로그인)은 개발 PoC 전용 — 다른 사람에게 답하는 봇에 개인 구독을 쓰는 것은 Anthropic 정책상 허용되지 않아 배포 앱에서는 막혀 있다 |

## 운영

### 맥북 호스트 모드 (개발 레포에서)

배포 앱은 이 일을 자동으로 합니다. 소스에서 돌릴 때는 macOS `launchd`가 봇·콘솔을 띄우고 관리합니다.

| | `askproject host on` 이후 |
| --- | --- |
| 실행 | 봇(`serve`)·웹 콘솔(`console`)이 바로 뜨고, 로그인할 때마다 자동으로 켜진다 |
| 장애 | 죽으면 1분 안에 다시 뜬다(키·크레딧 문제로 바로 죽으면 1분에 한 번만 재시도) |
| 잠자기 | `askproject power default\|lid-open\|lid-closed` — 봇이 스스로 `caffeinate -w`를 건다(봇이 죽으면 풀림). lid-closed는 전원 연결 시 덮개를 닫아도 깨어 있음 |
| 백업 | 매일 03:30 `askproject nightly`(backup → restore-check → maintenance) |
| 중복 | 한 맥에서는 봇이 하나만 뜨고, 다른 맥에서 같은 슬랙 앱이 켜지면 운영 채널 하트비트로 감지해 기다린다 |
| 로그 | `data/logs/{bot,console,backup}.log` |

### 메뉴 막대 앱

```bash
bash scripts/build-macos-app.sh     # 개발 앱: uv run으로 이 레포를 부름 → ~/Applications/AskProject.app
bash scripts/build-app.sh           # 배포 앱: 백엔드 내장 + Developer ID 서명 → dist/AskProject.app
```

| 영역 | 내용 |
| --- | --- |
| 머리 | 봇 상태(🟢 실행 중 · 🟠 재시작 대기 · ⚪ 꺼짐), **켜기/끄기** |
| 작업 | 봇 · 웹 콘솔 · 백업(매일 03:30) 각각의 상태 |
| 숫자 | 오늘 질문, 오늘 비용, 이번 주 비용, 확인 필요(누르면 콘솔 `/check`) |
| 최근 질문 | 슬랙 질문 5개(경로·확신도·비용·⚠︎), 누르면 콘솔 상세 |
| 경고 | 다른 맥에서 같은 봇 실행 중 · GitHub 토큰 만료 임박 · 답할 채널 미지정 · 자동 로그인 꺼짐 · 로그인 항목 승인 필요 |
| 아래 | 마지막 백업·지금 백업, 크레딧 확인, 웹 콘솔·로그·설정, 더 보기(마법사·진단 묶음·업데이트) |

앱은 화면만 맡고 백엔드 CLI(`status --json` 등)를 부릅니다. 상태 조회는 Claude를 부르지 않습니다(비용 0).

### 데이터 보존

- 감사 로그는 SQLite 파일 하나(`data/askproject.sqlite3`, 앱은 `~/Library/Application Support/AskProject/data/`)에 **영구 보존**합니다. 수정·삭제는 트리거가 막고, 개인정보가 섞이면 `redact`로 텍스트만 가립니다.
- 매일 백업 → 복구 확인 → 정리를 돌립니다. 절차는 [docs/operations.md](docs/operations.md).

## 보안

- 봇은 코드·문서·슬랙 모두 읽기 전용이다. 에이전트의 내장 도구(Bash, 파일 편집 등)는 꺼져 있고, 허용 목록 밖 도구는 훅에서 차단한다.
- 레포에 커밋된 파일(GitHub trees API 목록)만 인덱싱하고, `.env*`·키 파일은 설정과 무관하게 항상 차단한다(`.env.example`만 허용).
- 비밀값은 배포 앱에서 Keychain, 개발에서는 `.env`에만 둔다. GitHub 토큰은 API 요청 헤더로만 보내고, 레포 사본에는 `.git`이 없다.
- 봇이 답할 채널은 `ASKPROJECT_ALLOWED_CHANNELS`로 제한한다(GitHub 토큰 소유자의 읽기 권한이 그 채널 전원에게 열리므로).
- 웹 콘솔은 127.0.0.1에만 열리고, Host 헤더 검증 + 실행당 토큰으로 접근을 좁히며, 감사 로그를 읽기 전용으로 연다. 팀 공개는 Cloudflare Access JWT를 검증한다.
- 스크린샷은 저장하지 않는다. 화면 문구만 쓰고, 사용자 데이터로 보이는 값은 가린다.
- 흐름도·와이어프레임은 모델이 낸 명세(JSON)를 코드가 그린다. 모델 값은 이스케이프한 텍스트로만 들어가고 CSP로 외부 접근을 막는다.
- 배포 앱은 Developer ID 서명·Hardened Runtime·Apple 공증을 거친다.

취약점은 공개 이슈 대신 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요.

## 현재 상태와 한계

- 같은 질문도 스코핑이 빠른 답과 심층 탐색 사이에서 가끔 다르게 분류한다. 그래서 질문당 비용이 달라질 수 있다.
- 한 번에 레포 하나만 답에 쓴다. GitHub(github.com) 레포만 지원한다.
- 구조 맵 추출기는 패턴 기반이다 — api-client 템플릿 호출, NestJS 데코레이터, Next.js/Expo Router 파일 규칙, WebView 브리지 문자열. 맞지 않는 레포는 `repos.yaml`의 `structure:`를 생략한다(`find_screen`·`find_api`·`find_bridge`가 빈 결과).
- 와이어프레임은 저충실도다. 실제 디자인 시스템을 쓰지 않고 기존 화면의 구성 관례만 따른다.
- 화면·문서는 한국어다. 맥 앱은 Apple silicon 전용이다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [privacy.md](docs/privacy.md) | 데이터 흐름 전체, 로컬 보관 항목, 슬랙 사용자에게 알릴 것 |
| [packaging.md](docs/packaging.md) | 맥 앱: 번들 구조, 서명·공증, SMAppService, 마법사, 업데이트, 릴리스 CI |
| [operations.md](docs/operations.md) | DB 운영: 보존 정책, 백업·복구, 가리기, 스키마 변경, 외부 공개 |
| [deploy-vultr.md](docs/deploy-vultr.md) | 서버 배포 |
| [cost.md](docs/cost.md) | 비용 계산 방법, 경로별 실측, 월 비용 추정 |
| [implementation-plan.md](docs/implementation-plan.md) | 설계 결정과 구현 현황 |
| [web-console.md](docs/web-console.md) | 웹 콘솔 기획과 범위 |
| [learn.md](docs/learn.md) | 도메인 맵·용어 사전 학습: 초안·검증·잠금·적용, 학습 전후 eval 비교 |
| [wireframe.md](docs/wireframe.md) · [flowchart.md](docs/flowchart.md) | 와이어프레임·변경안 목업, 로직 흐름도 |

## 기여

이슈와 PR을 환영합니다. 개발 환경, 테스트, 커밋 규칙, 지켜야 할 원칙(읽기 전용 봇, 대상 레포 정보 커밋 금지)은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.

## 라이선스

[MIT](LICENSE). 배포 앱에는 제3자 구성 요소가 들어 있으며, 각자의 라이선스를 따릅니다. 특히 Claude Agent SDK에 포함된 `claude` 실행 파일은 Anthropic 소유이고 [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms)를 따릅니다(앱 안 `THIRD_PARTY_NOTICES.txt`). AskProject는 Anthropic의 공식 제품이 아닙니다.
