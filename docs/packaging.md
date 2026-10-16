# 데스크톱 제품화: 패키징·서명·배포

워크스페이스 관리자 한 명이 DMG를 받아 마법사만 따라 하면 셋업이 끝나는 macOS 앱(arm64 전용)이다.
터미널·git·Homebrew·Xcode 없이 동작한다. 서버 배포(docker compose)는 [deploy-vultr.md](deploy-vultr.md) 경로를 그대로 쓴다.

## 번들 구조

```
AskProject.app/Contents/
├─ MacOS/AskProjectBar            SwiftUI 메뉴 막대 앱 (마법사·설정·상태)
├─ MacOS/askproject-backend       C 런처 → Resources/backend/python/bin/python3.12 -I -B -m askproject
├─ Library/LaunchAgents/com.askproject.{bot,console,backup}.plist   SMAppService 에이전트 (BundleProgram = 런처)
├─ Frameworks/Sparkle.framework   앱 업데이트
└─ Resources/
   ├─ backend/python/             python-build-standalone 3.12 + site-packages(의존성·askproject·claude 바이너리)
   ├─ backend/bin/rg              ripgrep 공식 릴리스(정적 링크)
   ├─ backend/BUILD_INFO.json     버전·계약(protocol)·claude CLI·rg 버전
   └─ THIRD_PARTY_NOTICES.txt
```

데이터는 번들 밖에 둔다.

| 무엇 | 어디 | 비고 |
|---|---|---|
| 설정 | `~/Library/Application Support/AskProject/config/` | `settings.env`(일반 설정), `repos.yaml`·`domains.yaml`·`glossary.yaml`, `meta.json`(config_version) |
| 상태 | `…/AskProject/data/` | 감사 로그 DB, 호스트 ID, 콘솔 토큰, PAT 만료 기록, claude 전용 설정 폴더 |
| 백업 | `…/AskProject/backups/` | 매일 03:30 + 마이그레이션 직전(`pre-migrate/`) |
| 재생성 가능 | `~/Library/Caches/AskProject/repos/` | 레포 사본·트리. 지워도 다음 동기화가 다시 받는다 |
| 로그 | `~/Library/Logs/AskProject/{bot,console,backup}.log` | 5MB에서 copytruncate 회전, 3개 보관 |
| 비밀값 | 로그인 Keychain, 서비스 `com.askproject` | 슬랙·GitHub·Claude·노션 토큰 |

폴더는 0700, 설정 파일은 0600이다. 개발 모드(레포에서 `uv run`)는 예전 그대로 `.env`·`config/`·`data/`를 쓴다. 모드는 런처가 넣는 `ASKPROJECT_APP=1`로 정해진다. 경로를 정하는 곳은 [`paths.py`](../src/askproject/paths.py) 한 곳뿐이고, 앱은 `askproject paths --json`으로 같은 값을 읽는다.

## 빌드

```bash
bash scripts/build-backend-bundle.sh          # build/backend (PBS + 의존성 + rg), 약 380MB
bash scripts/build-app.sh                     # dist/AskProject.app 조립 + Developer ID 서명
NOTARY_PROFILE=askproject bash scripts/build-dmg.sh   # 공증 + 스테이플 + DMG
```

- `build-backend-bundle.sh`는 uv가 관리하는 PBS 인터프리터를 복사하고, venv 없이 그 site-packages에 `uv.lock` 기준으로 설치한다. venv의 `pyvenv.cfg`는 기반 인터프리터를 절대 경로로 가리켜서, 앱을 옮기면 깨지기 때문이다.
- 표준 라이브러리 중 tkinter·test·idlelib 등은 지우고, `__pycache__`는 `--invalidation-mode unchecked-hash`로 미리 컴파일한다. 실행할 때는 `-B`라서 번들에 아무것도 쓰지 않는다. 번들에 쓰면 서명이 깨진다.
- rg는 공식 릴리스(15.2.0)를 sha256으로 확인한다. Homebrew 판은 `/opt/homebrew/opt/pcre2` dylib에 묶여 있어 쓸 수 없다.
- Sparkle 2.10.0은 sha256을 고정해 받는다(`scripts/fetch-sparkle.sh`).
- `scripts/check-bundle.sh`는 번들에 `golden.yaml`·`.env`·`*.pem`·DB·회사별 YAML이 들어가지 않았는지 검사한다. 걸리면 빌드가 실패한다.

## 서명·공증

`scripts/sign-app.sh`는 안쪽부터 서명한다(Hardened Runtime + 타임스탬프).

| 대상 | 서명 | entitlement |
|---|---|---|
| `.so`·`.dylib`·`rg`·`python3.12` | 우리 Developer ID | 없음 — arm64 libffi는 정적 트램펄린이라 ctypes 콜백·cryptography가 entitlement 없이 동작 (W0 확인) |
| `claude` (claude-agent-sdk 휠) | **다시 서명하지 않음** — Anthropic PBC Developer ID(Q6L2SF6YDW) 그대로 | Anthropic이 넣은 `allow-jit`·`allow-unsigned-executable-memory` 등. 다시 서명하면 Bun 런타임이 JIT 메모리를 못 써서 죽는다. 스크립트가 서명 주체를 확인한다 |
| Sparkle XPC·Autoupdate·Updater.app | 우리 Developer ID | Downloader.xpc는 `--preserve-metadata=entitlements` |
| 런처·앱 | 우리 Developer ID | 앱: 없음(샌드박스 아님) |

공증 자격 증명은 한 번만 저장한다. 앱 암호는 사용자가 직접 입력한다.

```bash
xcrun notarytool store-credentials askproject --apple-id <Apple ID> --team-id AE57B3B397
```

**샌드박스는 쓰지 않는다**(Developer ID 배포). 임의 실행 파일(claude·rg) 하위 프로세스, launchd 에이전트, Keychain CLI가 필요하기 때문이다. App Store 배포는 범위 밖이다.

## 상시 실행 (SMAppService)

- 앱이 `SMAppService.agent(plistName:)`으로 번들 안 plist 세 개를 등록한다. `BundleProgram`이 번들 상대 경로라 앱을 옮기거나 업데이트해도 경로가 깨지지 않는다. plist에는 사용자 경로를 적을 수 없으므로 로그는 백엔드가 직접 `~/Library/Logs`로 보낸다(`logs.py`, `ASKPROJECT_JOB`).
- 잠자기 방지는 `serve`가 스스로 `caffeinate -i|-s -w <pid>`를 건다. 절전 모드를 바꾸면 봇만 재시작한다(plist는 정적).
- `/Applications` 밖(다운로드·DMG·App Translocation)에서 열면 옮기기를 권한다. 복사한 뒤 격리 속성(`com.apple.quarantine`)을 지우는데, 그러지 않으면 옮긴 뒤에도 번역 위치에서 실행된다.
- 개발 모드의 `askproject host on|off`(`~/Library/LaunchAgents`에 uv run plist 생성)는 그대로 둔다. 앱 모드에서 이 명령을 쓰면 거부한다. 레이블이 같아서 상태 조회(`launchctl print`)는 두 모드가 공유한다.

## 앱↔백엔드 계약

앱은 파일을 직접 쓰지 않는다. 계약 버전은 `askproject.__protocol__`이고, 앱의 `Backend.expectedProtocol`과 다르면 경고를 띄우고 켜기를 막는다.

| 명령 | 용도 |
|---|---|
| `status --json` | 메뉴 막대 상태(15초마다, Claude 호출 없음). 첫 실행 `configured:false`, PAT 만료, 허용 채널, 호스트 중복, 콘솔 키 |
| `paths --json` | 경로·모드 |
| `config get --json` | 설정(비밀값은 저장 여부만) |
| `config set --json` ← 표준 입력 | 검증(pydantic) + 원자적 쓰기(임시 파일 → rename). 비밀값은 Keychain(앱)·`.env`(개발). 비밀값은 명령 인자로 받지 않는다 |
| `setup manifest-url · pat-url · check slack/github/claude · repos · channels` | 마법사 |
| `doctor --online --json` | 점검 + 실패 항목의 마법사 단계(`step`) → '고치기' 버튼 |
| `migrate` | 아래 업데이트 절 |
| `diagnostics` | 비밀값을 가린 진단 zip |

## 설정 마법사

환영 → 슬랙 앱(매니페스트 미리 채움 `api.slack.com/apps?new_app=1&manifest_json=…`, xoxb·xapp 접두사 즉시 검증, `apps.connections.open`으로 앱 토큰 확인, 관리자 승인 대기 상태 저장·재개, 요청 문구 복사) → GitHub(PAT 미리 채움 `github.com/settings/personal-access-tokens/new?name=…&expires_in=90&contents=read[&target_name=조직]`, `/user/repos`로 레포 고르기) → Claude API 키(1토큰 확인) → 답할 채널(봇이 들어간 채널 체크·DM 허용·운영 채널) → `doctor --online` → 켜기(`sync` 후 SMAppService 등록, 로그인 항목 승인 안내).

진행 단계는 UserDefaults(`wizard.step`)에 저장한다. 슬랙 승인이 며칠 걸려도 이어서 할 수 있다.

## 거버넌스·보안

- **구독 모드 제거**: 앱 모드에서 `ASKPROJECT_LLM_PROVIDER=subscription`이면 Settings가 거부하고, `provider subscription`도 거부한다. 앱 화면에도 토글이 없다. 슬랙 봇은 다른 사람에게 답하므로 개인 구독 로그인을 쓰면 정책 위반이다. 개발 모드에서만 PoC로 남긴다.
- **답할 채널 허용 목록**(`ASKPROJECT_ALLOWED_CHANNELS`, DM은 `dm`): PAT 소유자의 레포 읽기 권한이 봇이 있는 모든 채널로 퍼지는 것을 막는다. 비면 전부 허용하되 시작할 때·doctor·메뉴 막대에서 경고한다. 허용 밖 채널에서는 "이 채널에서는 답하지 않아요"만 답한다.
- **호스트 1대**(`slack/heartbeat.py`): Socket Mode는 같은 앱 토큰의 연결들에 이벤트를 나눠 보낸다. 운영 채널(`host_channel`, 없으면 `escalation_channel`)에 호스트마다 메시지 하나를 두고 2분마다 제자리 갱신한다(메시지 metadata). 시작할 때 다른 호스트의 5.5분 이내 하트비트가 보이면 연결하지 않고 기다리고, `data/host_conflict.json` → 메뉴 막대 경고로 알린다. 운영 채널은 조용한 공개 채널을 권한다(최근 200개 메시지만 본다).
- **콘솔**: Host 헤더 검증(`127.0.0.1:포트`·`localhost:포트`만, DNS rebinding 차단)과 실행당 토큰을 쓴다. 앱이 `?key=`로 열면 HttpOnly·SameSite=Strict 쿠키로 바꾸고 주소에서 지운다. 토큰은 `data/console.json`(0600)에 둔다.
- **PAT 만료**: GitHub 응답 헤더 `github-authentication-token-expiration`을 `data/github_token.json`에 기록한다. 7일 전부터 status·doctor·메뉴 막대에서 경고한다.
- **claude 하위 프로세스 격리**: `cli_path`를 명시하고, `DISABLE_AUTOUPDATER=1`, 전용 `CLAUDE_CONFIG_DIR`(`data/claude`), `setting_sources=[]`을 준다. 사용자가 쓰는 Claude Code의 `~/.claude` 설정·훅·MCP·로그인이 봇에 섞이지 않는다.
- **Keychain**: `/usr/bin/security`로 쓰고 읽는다. 쓰기는 `security -i` 표준 입력으로 넘겨 ps에 보이지 않는다. 항목을 만든 `security`가 접근 목록에 들어가 백그라운드 에이전트가 대화 상자 없이 읽는다. 대신 같은 사용자의 다른 프로세스도 `security`로 읽을 수 있다(0600 파일과 같은 수준). 디스크·Time Machine에 평문으로 남지 않는다는 점이 `.env`보다 낫다. 로그인 Keychain은 사용자가 로그인해야 열리므로 자동 로그인이 필요하다.

## git 없이 레포 받기

`indexing/repo_sync.py`는 GitHub REST API만 쓴다.
- 기준 커밋: `GET /repos/{o}/{r}/commits/{ref}` (Accept `application/vnd.github.sha`). 이전과 같으면 아무것도 받지 않는다.
- 파일 목록·blob 해시: `GET /git/trees/{sha}?recursive=1`. `commit_files`와 코드 인덱스의 기준이고, 목록이 잘리면 받은 파일로 blob 해시를 직접 계산한다.
- 내용: `GET /tarball/{sha}`를 새 폴더에 풀고 바꿔 끼운다. 일반 파일·폴더만 풀고, 레포 밖 경로는 건너뛴다(tarfile `data` 필터가 두 번째 방어선).
- 바뀐 파일: 이전·새 트리의 blob 해시 비교.
- github.com만 지원한다(GHE는 `ASKPROJECT_GITHUB_API_URL`로 API 주소만 바꿀 수 있지만 URL 검증은 github.com 기준).

## 업데이트

- **앱**: Sparkle 2. `SU_FEED_URL`·`SU_PUBLIC_ED_KEY`를 넘겨 빌드했을 때만 켜진다. 이 레포는 비공개이므로 appcast·DMG는 **인증 없이 받을 수 있는 곳**(R2·S3 공개 버킷)에 둔다. 빌드 산출물에 내부 설정이 없다는 것은 `check-bundle.sh`가 보장한다.
  ```bash
  build/cache/Sparkle/bin/generate_keys          # 처음 한 번: 개인 키는 Keychain, 공개 키 출력
  SU_FEED_URL=https://…/appcast.xml SU_PUBLIC_ED_KEY=… NOTARY_PROFILE=askproject bash scripts/build-dmg.sh
  bash scripts/make-appcast.sh                   # dist/updates/appcast.xml → 공개 위치에 올림
  ```
- **교체 순서**: 설치 직전 `Agents.stopForUpdate()`(launchctl bootout)로 봇·콘솔을 멈춘다. 파이썬은 모듈을 필요할 때 읽어서, 교체 중인 번들의 새 파일과 옛 파일이 섞이는 것을 막기 위해서다. 새 버전 첫 실행 때 `askproject migrate`를 돌리고, 성공하면 등록된 에이전트를 다시 띄운다.
- **`askproject migrate`**: DB 스키마(`MIGRATIONS`, 추가만)와 설정 형식(`config_version`, `CONFIG_MIGRATIONS`)을 올린다.
  - DB·설정이 이 앱보다 새 버전이면 아무것도 바꾸지 않고 거부한다(다운그레이드 보호). `Store`도 새 DB는 열지 않는다(`SchemaTooNewError`).
  - 바꿀 것이 있으면 먼저 SQLite 온라인 백업을 한다(`backups/pre-migrate/`, WAL 내용 포함).
  - `data/migrate.lock`으로 한 프로세스만 실행한다. launchd 작업도 시작할 때 같은 잠금 아래 migrate를 부르므로, 재부팅으로 앱보다 먼저 떠도 안전하다.
- DB에는 절대 경로를 저장하지 않는다(파일 경로는 모두 레포 기준 상대). 그래서 데이터 위치 이동에 따른 스키마 변경은 없다.

## 라이선스

- claude-agent-sdk 파이썬 래퍼는 MIT다. [Agent SDK 문서](https://code.claude.com/docs/en/agent-sdk/overview)의 License and terms 절에 따르면, SDK 사용은 "자기 고객·최종 사용자에게 제공하는 제품·서비스를 구동할 때를 포함해" [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms)가 적용되고, 구성 요소에 별도 LICENSE가 있으면 그것을 따른다. 휠의 `_bundled/` 안에는 `claude` 바이너리만 있고 LICENSE 파일이 없으므로 Commercial Terms가 적용된다고 본다(2026-10-16 확인).
- SDK가 휠로 함께 배포하는 바이너리를 SDK 기반 제품 안에 그대로 넣는 것은 이 문구가 상정하는 사용이다. 다만 **바이너리 재배포를 명시적으로 허락하는 문구는 찾지 못했다.** 사내 사용은 문제 소지가 낮고, 다른 회사에 DMG를 배포하기 전에는 Anthropic(영업·법무)에 확인한다. 안 된다고 하면 첫 실행 때 SDK 공식 경로로 받는 방식으로 바꾼다.
- 같은 문서의 브랜딩 규칙: 제품을 "Claude Code"로 부르거나 Claude Code처럼 보이게 하면 안 된다("Powered by Claude"는 가능). 앱 이름·화면은 AskProject로 되어 있고, 고지 파일에서 구성 요소 이름으로만 언급한다. 같은 문서는 claude.ai 로그인(구독)을 제3자 제품에 넣는 것도 승인 없이는 허용하지 않는다고 한다. 배포 앱에서 구독 모드를 뺀 결정과 일치한다.
- ripgrep(MIT/Unlicense), CPython(PSF), 파이썬 의존성의 고지는 `THIRD_PARTY_NOTICES.txt`에 모은다(`scripts/collect-licenses.sh`).

## W0 스파이크 결과 (2026-10-16, 이 맥)

| 위험 | 결과 |
|---|---|
| PBS 번들이 git·셸 PATH 없이 뜨는지 | ✅ `env -i`로 실행. FTS5·OpenSSL 3.5·certifi 정상 |
| Hardened Runtime에서 cryptography·ctypes 콜백 | ✅ entitlement 없이 동작 |
| claude(Bun) 서명 | ✅ Anthropic 서명을 유지하면 그대로 실행(`2.1.300`) |
| 실행 중 `__pycache__` 쓰기 | ✅ 없음 — 실행 후에도 `codesign --verify --deep --strict` 통과 |
| SMAppService + BundleProgram | ✅ 서명한 시험 앱에서 등록 즉시 enabled(승인 요구 없음), 번들 상대 경로로 런처 실행, 해제 정상 |
| Gatekeeper·공증 | ✅ 앱·DMG 모두 공증 통과·스테이플, `spctl` → `accepted · Notarized Developer ID` (DMG 153MB) |
| 클린 맥(CLT·Homebrew 없음) | ⏳ 새 사용자 계정 또는 VM에서 확인 필요 |

## 알려진 함정

- `uv pip install --python <PBS>`는 PBS의 `EXTERNALLY-MANAGED` 표시를 지워야 한다(스크립트가 처리).
- 런처 이름을 `askproject`로 하면 대소문자를 구분하지 않는 APFS에서 `AskProjectBar`와 충돌할 수 있어 `askproject-backend`로 한다.
- `swiftc -typecheck`는 통과해도 `-O` 빌드에서만 잡히는 오류(배타적 접근 위반)가 있다. `build-app.sh`로 확인한다.
- launchd plist에 `StandardOutPath`를 쓰려면 절대 경로가 필요하다. 번들 plist는 정적이라 백엔드가 직접 로그를 연다.
- Sparkle.framework의 `Versions/Current`는 심볼릭 링크다. `find`에 `-H`가 없으면 안쪽 Updater.app·XPC를 못 찾아 다시 서명되지 않고, 공증이 "not signed with a valid Developer ID · no secure timestamp"로 거절된다.
- 로그인 Keychain은 로그인 전에는 잠겨 있다. 재부팅 후 자동 로그인이 꺼져 있으면 봇이 비밀값을 못 읽는다(메뉴 막대가 경고, FileVault와는 함께 쓸 수 없음).

## Sparkle 키 (2026-10-16 생성)

- 공개 키 `MCkJ16w/gJiBXaK9B95WSFfkWtpvnOY2dTDFo5Vn+3Q=` — `build-app.sh`의 기본값이다.
- 개인 키는 이 맥의 로그인 Keychain에만 있다(`generate_keys`가 저장). **잃으면 이미 배포한 앱에 업데이트를 보낼 수 없다.** `build/cache/Sparkle/bin/generate_keys -x <파일>`로 내보내 안전한 곳(비밀번호 관리자 등)에 보관한다.
- 업데이트를 켜려면 appcast 공개 주소 `SU_FEED_URL`만 정해 빌드하면 된다. 주소가 없는 빌드는 업데이트 확인을 하지 않는다.

## 클린 맥 검증 절차

개발 도구가 없는 맥에서 비개발자 경로를 처음부터 끝까지 확인한다. 새 macOS 사용자 계정(시스템 설정 → 사용자 및 그룹)이나 macOS VM(UTM 등)에서 한다. 이 맥의 개발자 계정에서는 Homebrew·uv·Keychain 항목 때문에 확인이 안 된다.

| # | 단계 | 확인할 것 |
|---|---|---|
| 1 | 환경 | 터미널에서 `xcode-select -p`가 실패(CLT 없음), `which brew uv rg git`이 비어 있음(`/usr/bin/git`은 설치 안내만 띄움 — 실행하지 말 것) |
| 2 | DMG 받기 | 브라우저로 내려받아(격리 속성이 붙게) 연다. "확인되지 않은 개발자" 경고 없이 열린다 |
| 3 | 옮기기 | DMG 안에서 바로 실행 → "응용 프로그램 폴더로 옮길까요?" → 옮기기 → 다시 열림. `/Applications/AskProject.app`에서 실행 중인지 확인 |
| 4 | 마법사 | 슬랙 앱 생성 링크가 매니페스트로 채워짐 · 토큰 접두사 즉시 검증 · 승인 대기 체크 후 앱 종료 → 다시 열면 같은 단계 |
| 5 | GitHub | PAT 링크가 이름·만료 90일·Contents read로 채워짐 · 레포 목록에서 선택 · 확인 통과 |
| 6 | Claude·채널 | API 키 확인(1토큰) · 채널 목록·DM·운영 채널 선택 |
| 7 | 점검·켜기 | 실패 항목의 '고치기'가 해당 단계로 감 · 켜기 → 레포 동기화(git 없이) → 로그인 항목 허용 알림 → 봇 실행 중 |
| 8 | 슬랙 | 허용 채널에서 멘션 질문에 답함 · 허용 안 된 채널에서는 거절 문구 · 운영 채널에 하트비트 메시지 1개 |
| 9 | 메뉴 막대 | 웹 콘솔 버튼으로 열림(토큰 쿠키) · 같은 주소를 토큰 없이 새 브라우저에서 열면 401 · 로그 폴더 열림 · 진단 묶음 생성 |
| 10 | 절전·재부팅 | 맥 운영에서 덮개 닫힘 선택 · 자동 로그인 켬 · 재부팅 → 로그인 없이 봇이 다시 응답 |
| 11 | 업데이트 | 빌드 번호를 올린 새 DMG로 앱을 바꿔 설치 → 설정·DB·Keychain 유지, `migrate` 후 봇 자동 재시작 |
| 12 | 두 번째 맥 | 다른 맥(또는 계정)에서 같은 토큰으로 켜면 나중 쪽이 기다리고 메뉴 막대에 경고 |
| 13 | 정리 | 앱을 휴지통으로 → 로그인 항목에서 사라지고 봇 프로세스 종료 |

## 남은 일 (사용자 작업 필요)

1. ~~공증 자격 증명 저장~~ (완료, 프로필 `askproject`)
2. 위 클린 맥 검증 절차
3. appcast 공개 위치(R2 등) 정하기 → `SU_FEED_URL`로 빌드, Sparkle 개인 키 백업
4. 다른 회사에 배포하기 전 Anthropic에 claude 바이너리 재배포 확인
