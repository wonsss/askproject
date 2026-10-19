# 데이터와 개인정보

**요약**: AskProject에는 개발자가 운영하는 서버가 없고, 텔레메트리·분석·오류 보고도 없습니다. 데이터는 봇을 설치한 맥(또는 서버)과 **설치한 사람이 직접 연결한 서비스**(Anthropic Claude API, Slack, GitHub, 선택한 경우 Notion) 사이에서만 오갑니다.

다만 "아무 데도 보내지 않는다"는 뜻은 아닙니다. 답을 만들려면 **질문과 관련 코드·문서 조각을 Anthropic의 Claude API로 보내야 합니다.** 아래 표가 전부이며, 각 항목은 코드 위치로 확인할 수 있습니다(2026-10-16 감사 기준).

## 밖으로 나가는 통신 전체

| 대상 | 무엇을 보내나 | 언제 | 코드 | 끄기 |
|---|---|---|---|---|
| **Anthropic Claude API** (`api.anthropic.com`) | 질문, 범위 판단용 도메인 요약·용어 사전, 에이전트가 도구로 읽은 **코드·문서 조각**, 첨부한 **스크린샷 이미지**, 시스템 프롬프트 | 질문할 때마다, 시작할 때 키 확인(1토큰) | `llm/complete.py`, `agent/claude_runner.py`, `pipeline/screenshot.py`, `doctor.py` | 끌 수 없음(봇의 핵심 기능). 주소는 `ANTHROPIC_BASE_URL`로 바꿀 수 있음 |
| **Slack API** | 답변·근거·흐름도·와이어프레임 파일 게시, 첨부 이미지 내려받기, 지정한 소스 채널 읽기, 운영 채널에 호스트 하트비트(맥의 호스트 이름) | 질문·버튼·동기화 때 | `slack/`, `indexing/slack_source.py`, `slack/heartbeat.py` | 소스 채널·운영 채널은 설정에서 비우면 꺼짐 |
| **GitHub API** (`api.github.com`) | 인증 토큰과 요청만 — 코드는 **받기만** 함 | 10분마다 동기화 | `indexing/repo_sync.py`, `setup.py` | — |
| **Notion API** (선택) | 인증 토큰과 요청만 — 페이지를 받기만 함 | 동기화 때 | `indexing/notion_source.py` | 노션 토큰을 넣지 않으면 꺼짐 |
| 업데이트 확인 (배포 앱, Sparkle) | appcast 주소에 HTTP 요청(앱 이름·버전이 User-Agent에 들어감). 시스템 정보는 보내지 않음(`SUEnableSystemProfiling` 끔) | 하루 한 번 | `Updater.swift` | `SU_FEED_URL` 없이 빌드하면 꺼짐 |
| **팀 공유 터널** (선택, Cloudflare Tunnel) | `cloudflared`가 Cloudflare로 나가는 연결을 열고, 팀원이 콘솔에서 보는 **질문·답·근거 화면이 Cloudflare를 거쳐** 전달됨(Cloudflare가 TLS를 종료하므로 내용을 볼 수 있는 위치). 터널 토큰은 환경변수로만 넘기고 다른 비밀값은 넘기지 않음 | 설정 → 팀 공유를 켰을 때 계속 | `tunnel.py`, `console/server.py` | 팀 공유를 끄거나 터널 토큰을 지우면 꺼짐(기본 꺼짐) |
| Cloudflare Access 공개 키 (선택) | 공개 키 요청만 — 팀원 로그인(Access JWT) 검증용 | 팀 공유를 켰을 때 | `console/server.py` | 팀 공유를 끄면 꺼짐 |
| 백업 업로드 (선택) | 감사 로그 DB 백업 파일 | 매일 03:30 | `ops.py` | `ASKPROJECT_BACKUP_UPLOAD_CMD`를 비우면 꺼짐(기본) |

이 밖의 주소로 나가는 코드는 없습니다. 웹 콘솔은 `127.0.0.1`에서만 열리고(팀 공유를 켜도 바깥 요청은 터널을 거쳐서만, Access 로그인을 통과해야 들어옴), 앱(메뉴 막대)은 로컬 백엔드 명령과 로컬 콘솔만 부릅니다.

## 일부러 끈 것

- **번들 `claude` 하위 프로세스의 부가 통신**: Claude Agent SDK에 들어 있는 `claude` 실행 파일은 기본 설정에서 사용 지표를 Anthropic과 제3자 로깅 서비스로 보냅니다([Claude Code 데이터 사용](https://code.claude.com/docs/en/data-usage)). 문서상 코드·프롬프트·경로는 포함되지 않지만, 봇에는 필요 없으므로 모두 끕니다: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_FEEDBACK_COMMAND`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`, `DISABLE_AUTOUPDATER` (`agent/claude_runner.py`의 `QUIET_ENV`, 테스트 `test_claude_subprocess_sends_no_telemetry`).
- 사용자의 `~/.claude` 설정·훅·MCP 서버가 봇에 섞이지 않도록 전용 `CLAUDE_CONFIG_DIR`을 씁니다.
- 에이전트에는 읽기 전용 도구만 있습니다. 셸·파일 쓰기·웹 접속 도구가 없어서, 코드나 문서 속에 지시문이 숨어 있어도 다른 곳으로 데이터를 보낼 수단이 없습니다.

## Anthropic으로 가는 데이터

- 사용하는 것은 설치한 사람의 **API 키**입니다. 데이터 처리는 그 계정의 [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms)와 개인정보 처리방침을 따릅니다. Commercial Terms는 API로 보낸 고객 콘텐츠를 모델 학습에 쓰지 않는다고 정하고 있습니다. 보존 기간·Zero Data Retention 같은 세부 사항은 Anthropic 문서와 계약을 확인하세요.
- 보내는 코드는 질문과 관련해 에이전트가 고른 조각이고, 레포 전체가 아닙니다. 다만 범위 안이라면 어떤 파일이든 보낼 수 있습니다. `.env*`·키 파일은 설정과 무관하게 항상 읽지 않습니다(`indexing/filters.py`).
- 개인 Claude 구독 로그인은 배포 앱에서 쓸 수 없습니다(다른 사람에게 답하는 봇이라 Anthropic 정책상 API 키만 허용).

## 이 맥(또는 서버)에 남는 것

| 무엇 | 어디 | 보존 |
|---|---|---|
| 감사 로그: 질문·답·근거·도구 호출·피드백·비용 | `data/askproject.sqlite3` (앱: `~/Library/Application Support/AskProject/data/`) | **영구**. 개인정보가 들어왔으면 `askproject redact`로 텍스트를 가림([operations.md](operations.md)) |
| 레포 사본 | `data/repos/` (앱: `~/Library/Caches/AskProject/`) | 다음 동기화 때 갱신, 지워도 다시 받음 |
| 로그 | `data/logs/` (앱: `~/Library/Logs/AskProject/`) | 5MB마다 회전, 3개 |
| 비밀값 | `.env` (앱: 로그인 Keychain `com.askproject`) | 설정에서 지우기 전까지 |
| 백업 | `backups/` | 14일 |

진단 묶음(`askproject diagnostics`)은 이 맥에 zip으로만 만들어지며, 토큰 형식은 가리지만 레포 이름·채널 ID는 들어 있습니다. 보낼지는 직접 정합니다.

## 슬랙 사용자에게 알릴 것

봇을 들인 채널의 멤버에게 다음을 알리는 것을 권합니다. 봇은 질문 처리 중 메시지에 "질문과 관련 코드는 Claude API로 처리돼요"라고 표시합니다.

- 봇에게 한 질문과 첨부 이미지, 관련 코드·문서 조각은 답을 만들려고 Anthropic Claude API로 전송됩니다.
- 질문과 답은 감사 로그에 영구 보관되고, 관리자가 웹 콘솔에서 볼 수 있습니다.
- 고객 개인정보·비밀번호 등은 질문에 넣지 마세요.

## 직접 확인하는 법

- 위 표의 코드 위치를 읽거나, 네트워크 모니터(Little Snitch, LuLu 등)로 봇 프로세스의 연결 대상을 보세요. 정상이라면 `api.anthropic.com`, `slack.com`·`wss-*.slack.com`, `api.github.com`·`codeload.github.com`(+ 선택한 Notion·업데이트 주소, 팀 공유를 켰다면 `cloudflared`의 Cloudflare 연결)뿐입니다.
- 문제를 발견하면 [SECURITY.md](../SECURITY.md)의 방법으로 알려 주세요.
