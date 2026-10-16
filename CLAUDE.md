# CLAUDE.md

AskProject: 기획자가 슬랙에서 질문하면 대상 코드베이스와 기획 문서(레포 docs·노션·슬랙 채널)를 근거로 답하는 슬랙 봇. 특정 서비스에 묶여 있지 않은 범용 구조다 — 대상 레포는 `config/repos.yaml`, 도메인 분류는 `config/domains.yaml`, 용어 사전은 `config/glossary.yaml`로 서비스마다 채운다.

## 먼저 읽을 것

- `README.md` — 구조, 실행 명령, 슬랙·노션 설정
- `docs/implementation-plan.md` — 설계 결정과 구현 현황(0절)
- `docs/deploy-vultr.md` — 배포
- `docs/cost.md` — 경로별 비용 실측, 월 비용 추정, 모델·effort 판단
- `docs/wireframe.md` — 와이어프레임 생성 동작·보안·비용
- `docs/operations.md` — DB 운영: 보존 정책(영구), 백업·복구, 가리기, 스키마 변경, 외부 공개, 맥 간 이전
- `docs/flowchart.md` — 정책·흐름 답변의 로직 흐름도(명세 JSON + SVG 렌더러)
- `docs/packaging.md` — 데스크톱 제품화: 번들(PBS+uv)·서명·공증·SMAppService·마법사·업데이트·migrate

## 명령

`make`로 자주 쓰는 명령 목록을 본다(test·doctor·sync·serve·console·version·release·app·dmg).

```bash
uv run askproject doctor --online   # 환경·연결 점검
uv run askproject sync              # 레포 동기화 + 코드·구조·문서 인덱싱 + 노션·슬랙 수집
uv run askproject ask "질문" [--domains <id>]
uv run askproject eval [--ids q01] [--limit N]   # eval/golden.yaml → eval/results/
uv run askproject serve             # 슬랙 봇 (Socket Mode)
uv run askproject stats
uv run askproject console           # 웹 콘솔 127.0.0.1:8787 (읽기 전용, API 비용 없음)
uv run askproject backup | restore-check | maintenance   # DB 운영 (docs/operations.md)
uv run askproject backups [--json] | restore <백업 파일> --yes [--json]   # 백업 목록 · 백업으로 DB 교체(봇·콘솔 끈 뒤)
uv run askproject tunnel              # 웹 콘솔 팀 공유 터널 (설정 → 팀 공유, launchd 작업용)
uv run askproject host on|off|status   # 맥북 호스트 모드 (launchd, 잠자기 방지, 매일 백업)
uv run askproject status [--json]      # 메뉴 막대 앱용 상태 (Claude 호출 없음)
uv run askproject provider [api|subscription]  # LLM 인증 경로 확인·전환
bash scripts/build-macos-app.sh     # 메뉴 막대 앱 macos/AskProjectBar → ~/Applications/AskProject.app
uv run askproject redact <query_id> --reason "…"        # 개인정보 유입 시 텍스트 가림
uv run askproject migrate | diagnostics | nightly     # 스키마·설정 올리기(백업 후) · 진단 zip · 매일 작업
uv run askproject paths|config get|status --json      # 앱↔백엔드 계약(JSON). config set은 표준 입력 JSON
uv run askproject setup manifest-url|pat-url|check <slack|github|claude>|repos|channels   # 마법사용
bash scripts/build-app.sh            # 배포 앱(백엔드 내장 + Developer ID 서명) → dist/AskProject.app (SIGN=0이면 dist/unsigned/)
NOTARY_PROFILE=askproject bash scripts/build-dmg.sh   # 공증 + DMG
make release [V=1.2.3] · make release-minor · make release-major   # 버전 올리기·커밋·태그·push → CI가 DMG 빌드·공증·Release (GitHub Actions 'Run workflow' 버튼도 같음, Secrets는 docs/packaging.md)
python3 scripts/bump_version.py --check            # 버전 네 곳 일치 확인 (손으로 고치지 않는다)
uv run pytest
```

## 구조 요약

`src/askproject/`
- `app.py` 조립 지점 · `cli.py` · `doctor.py` · `evaluation.py`
- `paths.py` 실행 모드별 경로(개발: 레포 상대, 앱 `ASKPROJECT_APP=1`: ~/Library) · `keychain.py` 앱 모드 비밀값 · `config_store.py` 앱용 설정 계약(검증·원자적 쓰기) · `setup.py` 마법사 점검 · `migrate.py` · `diagnostics.py` · `logs.py`
- `pipeline/` 스코핑(`scoping.py`) → 라우팅(거절·되묻기·단순·에이전트·와이어프레임) → `answer.py`(인용 검증·확신도) · `prompts.py` · `screenshot.py` · `wireframe.py`(화면 명세 스키마·결정적 렌더러, 자유 HTML 허용 목록 정제·CSP)
- `agent/` `AgentRunner` 포트 + Claude Agent SDK 구현(내장 도구 끔, 읽기 전용 도구만, PreToolUse 훅 차단)
- `tools/` code_search·read_code·list_dir · find_screen·find_api·find_bridge · search_docs·read_doc
- `indexing/` repo_sync(GitHub REST API: tarball·trees, git 불필요, PAT 만료 기록) · code_index · structure(API·라우트·manifest·브리지 맵, 레포별 규칙은 repos.yaml `structure:`) · docs_index · notion_source · slack_source
- `store/db.py` SQLite. 감사 로그 테이블은 트리거로 append-only
- `console/` 웹 콘솔(Starlette, 감사 로그 읽기 전용 연결, 최신성은 `commit_files` 해시 비교, Host 검증 + 실행당 토큰, 공개 호스트는 Cloudflare Access JWT)
- `slack/heartbeat.py` 워크스페이스 호스트 1대 감지(운영 채널 메시지 metadata)
- `macos/` 앱(SwiftUI, 파일을 직접 쓰지 않고 CLI JSON만 부름) · `launcher/`(C 런처) · `LaunchAgents/`(SMAppService plist) · `entitlements/`
- `config/` repos.yaml · domains.yaml · glossary.yaml · `eval/golden.yaml`(형식은 `eval/golden.example.yaml`)

## 지켜야 할 것

- 봇은 모든 소스에 **읽기 전용**이다. 에이전트에 쓰기·셸·외부 전송 도구를 추가하지 않는다.
- `.env`, `secrets/`, `data/`는 커밋하지 않는다(.gitignore). 비밀값을 출력·로그·커밋 메시지에 남기지 않는다.
- 이 레포는 공개해도 되는 범용 코드만 담는다. 대상 레포는 **비공개**일 수 있으므로, 그 내부 정보(채운 `config/*.yaml`, `eval/golden.yaml`, 실제 답변 스크린샷·코드 경로·커밋 해시·서비스 이름)는 커밋하지 않는다. `config/*.yaml`은 빈 템플릿만 커밋하고, 문서·테스트 예시는 가상의 서비스(주문·알림 등)로 쓴다.
- 커밋은 작업 단위로 나누고, 파일 경로를 명시해 스테이징한다(`git add -A` 금지). push는 사용자 확인 후에만.
- 골든 세트 정답을 쓸 때는 대상 레포 코드를 직접 읽어 근거 라인을 확인한다.

## 현재 상태

- 비용은 SDK 보고값이 아니라 토큰 × 단가로 계산한다(`llm/pricing.py`). 경로별 실측과 월 비용 추정은 `docs/cost.md`.
- **API 비용이 드는 작업(eval·ask)은 예상 금액을 먼저 사용자에게 말한다.** (api 모드 기준 전체 평가 1회 약 $0.85 — 문항 수에 비례)
- 모델: `claude-sonnet-5-5`(심층 탐색·판정), `claude-haiku-5-5`(스코핑·빠른 답). 소형 모델 호출은 thinking을 끈다(켜면 스코핑 JSON이 잘림). effort는 `ASKPROJECT_AGENT_EFFORT_LARGE/SMALL`로 바꿀 수 있고 비우면 모델 기본값.
- 와이어프레임 생성(화면 명세 JSON → 렌더러가 저충실도 HTML, 슬랙 파일 업로드). 상세는 `docs/wireframe.md`. 정책·흐름 질문에는 로직 흐름도(SVG HTML). 상세는 `docs/flowchart.md`.
- 답변 끝에 '💬 이어서 물어보기' 후속 질문 버튼 2~3개.
- 웹 콘솔(`askproject console`): 동기화 때 커밋별 파일 해시(`commit_files`)를 기록해 답의 최신성을 판단한다. 외부 공개는 Cloudflare Tunnel + Access(`docs/operations.md`). 배포 앱은 설정 → 팀 공유(대시보드 터널 토큰, 내장 cloudflared, 에이전트 `com.askproject.tunnel`, `tunnel.py`, `setup check tunnel`).
- DB: 감사 로그 **영구 보존**. 스키마는 `PRAGMA user_version` + `store/db.py`의 `MIGRATIONS`(추가만, 현재 v3). 백업·복구 확인·정리·가리기·복구(`restore`) 명령 있음. 맥 밖 사본은 백업 사본 폴더(`ASKPROJECT_BACKUP_COPY_DIR`, 동기화 폴더에 원자적 복사) 또는 `ASKPROJECT_BACKUP_UPLOAD_CMD`. 앱은 패널 백업 폴더 열기, 설정 → 맥 운영에서 사본 폴더 고르기·'백업에서 복구'(`RestoreView.swift`).
- 메뉴 막대 앱(SwiftUI `macos/AskProjectBar`, 화면만 맡고 CLI를 부름). 맥북 호스트 모드(`host.py`, launchd `com.askproject.{bot,console,backup}`)와 봇 중복 실행 잠금(`data/serve.lock`).
- 배포 앱(DMG, `docs/packaging.md`): PBS 파이썬+의존성+rg 내장, Developer ID 서명(claude 바이너리는 Anthropic 서명 유지), SMAppService 에이전트, 첫 실행 마법사, Sparkle. 데이터는 ~/Library(Application Support·Caches·Logs) + Keychain. **앱 모드에서는 구독(subscription) 모드를 거부한다.** 앱↔백엔드 계약 버전 `askproject.__protocol__`.
- 거버넌스: 답할 채널 허용 목록(`ASKPROJECT_ALLOWED_CHANNELS`, DM은 `dm`), 호스트 하트비트(`ASKPROJECT_HOST_CHANNEL`), PAT 만료 7일 전 경고. 에이전트 claude 하위 프로세스는 `cli_path` 명시·`DISABLE_AUTOUPDATER`·전용 `CLAUDE_CONFIG_DIR`로 사용자 ~/.claude와 격리.
- LLM 인증 경로는 `ASKPROJECT_LLM_PROVIDER`로 전환(`api` 기본 | `subscription`=로컬 `claude` CLI 구독 로그인을 하위 프로세스로, `llm/claude_cli.py`). `askproject provider api|subscription` 또는 메뉴 막대의 API|구독 토글로 바꾼다. 답에 실제 쓰인 경로는 `query_results.provider`에 기록돼 슬랙 메타 줄·콘솔 칩·앱 헤더에 표시된다. 구독 모드는 `max_budget_usd`를 넘기지 않는다(CLI가 리스트 단가로 세다 보니 일찍 끊김, `max_turns`만 제한). 구독으로 타인에게 서비스하는 것은 Anthropic 정책상 불가 — 로컬 PoC 전용.
- 에이전트 SDK 하위 프로세스는 부모 환경변수를 물려받는다. 그래서 Claude 키·주소는 셸 값을 무시하고 `.env`만 쓴다(`ANTHROPIC_API_KEY`·`ANTHROPIC_BASE_URL`, 하위 프로세스의 `ANTHROPIC_AUTH_TOKEN`은 비움). `serve`는 시작할 때 1토큰 호출로 키·크레딧을 확인하고, 문제가 있으면 원인을 출력하고 멈춘다.
