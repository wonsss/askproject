# DB 운영 런북

AskProject의 데이터는 SQLite 파일 하나(`data/askproject.sqlite3`)에 있다. 이 문서는 배포 후 그 파일을 어떻게 지키고, 정리하고, 문제가 생기면 어떻게 되돌리는지 정리한다. 여기 나오는 명령은 모두 Claude API를 쓰지 않는다(비용 0).

## 무엇이 어디에 있나

| 데이터 | 위치 | 잃으면 |
| --- | --- | --- |
| **감사 로그**: 질문·답·근거·탐색 과정·피드백·비용·가림 기록 | `data/askproject.sqlite3` (Docker: 볼륨 `askproject-data`) | **복구 불가.** 백업이 유일한 사본 |
| 코드·문서 인덱스, 구조 맵, 커밋별 파일 해시 | 같은 파일 | `askproject sync`로 다시 만든다. 단, 예전 커밋 해시는 사라져 옛 답의 최신성 판단이 ❔로 바뀐다 |
| 대상 레포 클론 | `data/repos/` | `askproject sync`로 다시 받는다 |
| 백업 | `backups/` (Docker: 호스트 `./backups`) + 맥 밖 사본(백업 사본 폴더 또는 업로드 명령) | 맥 밖 사본이 있으면 괜찮다 |

`data/`, `backups/`는 git에 올리지 않는다(`.gitignore`). 질문에 비공개 코드 정보가 들어 있기 때문이다.

**배포 앱(DMG)** 은 같은 것을 레포 밖에 둔다. `askproject paths --json`이 실제 경로를 알려 준다([packaging.md](packaging.md)).

| 데이터 | 배포 앱 위치 |
| --- | --- |
| 감사 로그 DB·상태 | `~/Library/Application Support/AskProject/data/` |
| 설정(`settings.env`·YAML·`meta.json`) | `~/Library/Application Support/AskProject/config/` |
| 백업 | `~/Library/Application Support/AskProject/backups/` (마이그레이션 직전 `pre-migrate/`, 복구 직전 `pre-restore/`) |
| 복구 전 DB | `…/data/askproject.sqlite3.before-restore-<시각>` (`-wal`·`-shm`도 같은 접미사, 지우지 않음) |
| 레포 사본(재생성 가능) | `~/Library/Caches/AskProject/repos/` |
| 로그 | `~/Library/Logs/AskProject/` |
| 비밀값 | 로그인 Keychain, 서비스 `com.askproject` |

## 보존 정책

**감사 로그는 영구 보존한다.** (2026-10-16 결정)

- 질문·답·근거·피드백 행은 지우지 않는다. 테이블 트리거가 수정·삭제를 막는다(append-only).
- 로컬 백업 파일만 `ASKPROJECT_BACKUP_KEEP_DAYS`(기본 14일)가 지나면 지운다. 백업 사본 폴더는 `ASKPROJECT_BACKUP_COPY_KEEP_DAYS`(기본 90일)가 지난 `askproject-*.sqlite3`(+`.sha256`)만 지운다 — 동기화 폴더 용량(iCloud 무료 5GB 등)이 한정돼 있어서다. 그 폴더의 다른 파일은 건드리지 않는다. 업로드 명령으로 보낸 복사본의 보관 기간은 저장소의 수명 주기 설정으로 정한다(권장: 90일 이상).
- `pre-migrate/`·`pre-restore/` 백업은 365일, 복구 때 옮겨 둔 `before-restore-*` 파일은 자동으로 지우지 않는다(확인 뒤 손으로 지운다).
- 정리 대상은 감사 로그가 아니다. 답변이 참조하지 않는 오래된 **커밋 스냅숏**과 WAL 파일만 정리한다.
- 개인정보가 섞여 들어온 경우에만 **가리기**(`askproject redact`)를 쓴다. 행은 남기고 텍스트만 가린다.

정책을 바꿀 때(예: 1년 뒤 질문 문장 익명화)는 이 절과 `askproject maintenance`를 함께 고친다.

## 매일 자동으로 하는 일 (Docker `backup` 서비스 또는 맥북 호스트 모드)

맥북 호스트 모드(`askproject host on`)와 배포 앱(SMAppService)에서는 launchd가 매일 03:30에 같은 순서로 실행한다. 로그는 `backup.log`(개발 `data/logs/`, 배포 앱 `~/Library/Logs/AskProject/`)에 남고, 맥북이 그 시각에 꺼져 있으면 건너뛴다.

```
askproject nightly        # = backup → restore-check → maintenance (앞 단계가 실패하면 멈춤)
```

| 단계 | 하는 일 |
| --- | --- |
| `backup` | SQLite 온라인 백업(봇이 쓰는 중에도 일관된 스냅숏) → 무결성 확인 → `.sha256` 기록 → 14일 지난 로컬 백업 정리 → 백업 사본 폴더에 복사 → `ASKPROJECT_BACKUP_UPLOAD_CMD`로 서버 밖 복사 |
| `restore-check` | 방금 만든 백업을 읽기 전용으로 열어 무결성·스키마 버전·질문 수 확인 |
| `maintenance` | 답변이 참조하지 않는 커밋 스냅숏 삭제, WAL 체크포인트, 통계 갱신 |

하나라도 실패하면 `backup failed`를 남긴다(`docker compose logs backup`).

### 맥 밖 사본: 백업 사본 폴더 (배포 앱·맥북 호스트 권장)

백업이 같은 맥(서버) 디스크에만 있으면 맥을 잃을 때 함께 사라진다. 가장 쉬운 방법은 iCloud Drive·Google Drive 같은 **동기화 폴더**를 고르는 것이다. 앱의 설정 → 맥 운영 → 백업 사본 폴더에서 고르거나(`config set`이 검증: 있는 폴더, 쓰기 가능, 백업 폴더 자체·하위는 거부), 개발 모드는 `.env`에 쓴다.

```
ASKPROJECT_BACKUP_COPY_DIR=~/Library/Mobile Documents/com~apple~CloudDocs/AskProject
ASKPROJECT_BACKUP_COPY_KEEP_DAYS=90   # 이 폴더의 오래된 askproject-*.sqlite3만 정리
```

- 백업할 때마다(매일·'지금 백업') 로컬 백업을 끝낸 뒤 `.sqlite3`, 그다음 `.sha256`를 숨김 임시 이름(`.<이름>.partial`)으로 복사하고 rename한다. 동기화 앱이 반쯤 쓴 파일을 올리지 않게 하기 위해서다. `.sha256`가 보이면 본 파일은 이미 완성돼 있다.
- 폴더가 없으면(동기화 앱 종료·외장 디스크 분리) 로컬 백업은 남기고 `backup`이 실패로 끝난다(`nightly`는 거기서 멈춘다).
- 상태·`doctor`는 **가장 최근 백업과 같은 이름의 사본이 그 폴더에 실제로 있을 때만** 맥 밖 사본으로 인정한다(업로드 명령이 설정돼 있으면 그것도 인정).
- 사본에는 질문·답 원문이 들어 있다. 팀 공유 폴더가 아닌 개인 폴더를 고른다.

### 서버 밖 복사 설정 (업로드 명령)

서버(Docker)처럼 동기화 폴더가 없는 곳에서는 업로드 명령을 쓴다. 백업 사본 폴더와 함께 써도 된다.

```
# .env 예시 — {file}이 백업 파일 경로로 바뀐다. 셸을 거치지 않으므로 파이프·변수 확장은 쓸 수 없다.
ASKPROJECT_BACKUP_UPLOAD_CMD=rclone copy {file} r2:askproject-backups
# 또는
ASKPROJECT_BACKUP_UPLOAD_CMD=aws s3 cp {file} s3://askproject-backups/
```

업로드 도구(rclone, aws)는 백업 컨테이너 이미지에 설치해야 한다. 저장소 쪽 자격 증명은 그 도구의 설정으로 넣고, AskProject `.env`에는 넣지 않는다.

## 점검 (`askproject doctor`)

| 항목 | 기준 |
| --- | --- |
| DB 무결성 | `quick_check`가 `ok` |
| DB 스키마 | 코드의 `SCHEMA_VERSION`과 같음 |
| DB 용량 | 디스크 여유 2GB 이상 |
| 최근 백업 | 36시간 이내, 최근 백업의 맥 밖 사본 있음(사본 폴더) 또는 업로드 명령 설정됨 |

주 1회 `doctor`를 돌리거나, 배포 환경의 모니터링에서 `backups/`의 최신 파일 시각을 본다.

## 복구 절차

복구는 **DB 파일 교체**다. 감사 로그 행을 지우지 않으므로 append-only 트리거와 부딪히지 않고, 지금 DB도 지우지 않고 옆에 옮겨 둔다.

**앱에서**: 패널 '더 보기' 또는 설정 → 맥 운영 → **백업에서 복구…** → 백업 고르기(목록에 없으면 '다른 파일 고르기'로 사본 폴더·다른 맥의 파일) → 자동 확인 → 확인 대화상자 → 앱이 봇·콘솔·매일 백업을 멈추고(배포 앱: 켜져 있던 SMAppService 작업만 bootout, 개발 앱: `host off`) `restore`를 부른 뒤 켜져 있던 것만 다시 켜고(배포 앱은 `migrate` 먼저) `doctor` 결과를 보여 준다. 복구가 실패해도 다시 켠다.

**CLI에서**:

1. 백업을 고른다. `askproject backups`(매일·`pre-migrate/`·`pre-restore/`·사본 폴더에만 있는 것, sha256 일치·질문 수). 맥을 잃었으면 새 맥에서 사본 폴더나 서버 밖 복사본의 파일을 쓴다.
2. 복구 전에 확인한다(무결성·스키마·sha256 — 옆에 `.sha256`가 있을 때).
   ```bash
   uv run askproject restore-check backups/askproject-20261016-001542.sqlite3     # 앱은 --json
   ```
3. 봇·콘솔을 멈춘다(`askproject host off`, `docker compose stop askproject`, 또는 `Ctrl+C`).
4. 복구한다.
   ```bash
   uv run askproject restore backups/askproject-20261016-001542.sqlite3 --yes     # 앱은 --json
   ```
   `restore`가 하는 일:
   - 2와 같은 확인. 문제가 있으면 아무것도 바꾸지 않는다.
   - `data/serve.lock`·`data/migrate.lock`을 잡는다. 봇이 잠금을 쥐고 있거나 웹 콘솔(`data/console.json`의 pid)이 살아 있으면 거부한다. 잠금은 끝날 때까지 쥐고 있어 도중에 봇이 뜨지 않는다.
   - 지금 DB를 `backups/pre-restore/`에 한 번 더 온라인 백업한다(망가진 DB라 실패해도 다음 단계에서 원본을 옮겨 두므로 계속한다).
   - 지금 DB와 `-wal`·`-shm`을 `data/askproject.sqlite3.before-restore-<시각>`(+`-wal`, `-shm`)으로 옮긴다. 접미사를 맞춰 두어 그 묶음도 SQLite로 그대로 열린다.
   - 백업을 `data/.askproject.sqlite3.restoring`으로 복사한 뒤 rename으로 제자리에 둔다. 복사가 실패하면 옮긴 파일을 되돌린다.
5. 봇을 시작하고 `askproject doctor`로 확인한다. 백업 이후 질문은 화면에서 사라진 상태다(옮겨 둔 `before-restore-*`에는 남아 있다). 예전 스키마의 백업이면 `migrate`(또는 launchd 작업 시작)가 먼저 올린다.
6. 인덱스는 백업에 들어 있으므로 바로 동작한다. 최신 코드로 맞추려면 `askproject sync`.

**연습:** 파일럿 시작 전에 한 번, 이후 분기마다 한 번 다른 폴더에서 4~5단계를 해 본다.

## 개인정보가 들어왔을 때 (`askproject redact`)

```bash
uv run askproject redact <query_id> --reason "질문에 실명·연락처 포함"
```

- 질문 문장, 답변, 탐색 과정의 도구 입력을 `[삭제됨]`으로 바꾼다. 근거 경로·비용·피드백 행은 남는다.
- 누가, 언제, 왜 가렸는지 `redactions` 테이블에 남고, 이 기록도 지울 수 없다.
- 트리거는 쓰기 잠금을 건 한 트랜잭션 안에서만 잠시 내렸다가 다시 건다. 도중에 실패하면 전부 되돌린다.
- `query_id`는 웹 콘솔 질문 상세의 고정 링크(`/q/<id>`)에서 찾는다.
- **백업에는 가리기 전 내용이 남는다.** 완전히 지워야 하면 그 기간의 로컬·서버 밖 백업도 확인한다.
- 슬랙 원본 메시지는 별도다. 슬랙에서 지운다.

## 스키마 변경

- DB에는 스키마 버전(`PRAGMA user_version`)이 있다. 현재 v3(v1 기준 테이블 + v2 가림 기록 + v3 `query_results.provider` 컬럼 — 답변에 쓰인 인증 경로 api|subscription).
- 테이블을 바꿀 때는 `src/askproject/store/db.py`의 `MIGRATIONS`에 번호를 붙여 **추가만** 한다. 이미 배포한 항목은 고치지 않는다.
- `askproject migrate`가 서비스 시작 전에 한 번에 올린다. 바꿀 것이 있으면 먼저 `backups/pre-migrate/`에 온라인 백업을 하고, `data/migrate.lock`으로 한 프로세스만 실행한다. 배포 앱은 켜기·업데이트 직후 이 명령을 부르고, launchd 작업도 시작할 때 같은 잠금 아래 부른다.
- 개발 모드에서는 지금처럼 `Store`를 여는 쪽이 모자란 마이그레이션을 적용한다.
- **다운그레이드 거부**: DB 버전이 코드가 아는 것보다 높으면(더 새 앱이 만든 DB) `migrate`는 아무것도 바꾸지 않고 멈추고, `Store`도 열지 않는다(`SchemaTooNewError`). 예전 앱으로 되돌리려면 `pre-migrate/` 백업으로 복구해야 한다.
- 설정 파일 형식은 `config/meta.json`의 `config_version`(현재 1)과 `migrate.py`의 `CONFIG_MIGRATIONS`로 같은 방식으로 관리한다.

## 다른 맥으로 이전

**배포 앱**: 새 맥에 DMG로 설치하고 마법사를 다시 한다. Keychain 비밀값은 옮기지 않고 새로 붙여넣는다(토큰을 파일로 옮기지 않기 위해). 답변 이력을 살리려면 예전 맥의 `~/Library/Application Support/AskProject/`를 앱을 끈 상태에서 통째로 복사한다. 그러면 하트비트용 호스트 ID도 따라오니, 예전 맥은 반드시 끈다. 끄지 않으면 운영 채널의 하트비트로 서로를 감지해 나중에 켠 쪽이 기다린다.

**개발 레포**: 봇의 몸통은 레포+`.env`+DB+launchd이고, 메뉴바 앱은 조종판이라 따로 옮기지 않는다. 새 맥에서는 이렇게 세팅한다.

1. **준비물을 옮긴다** — 클라우드 동기화가 아니라 `scp`/AirDrop 등으로 직접 보낸다(비밀값 포함).
   - 레포 클론(`git clone`), `uv`(brew)
   - `.env` 전체, `secrets/github-app.pem`
   - `data/askproject.sqlite3`(답변 이력을 살릴 때만 — 안 옮기면 `askproject sync`로 새로 시작)
   - 터널: `~/.cloudflared/<tunnel-id>.json` + `config.yml` (로그인 없이 자격증명 파일만 있으면 `tunnel run`이 동작한다. 양쪽 맥에서 동시에 띄워도 되고, 되면 Cloudflare가 커넥터 둘에 분산한다)
2. **새 맥에서**
   ```bash
   uv sync && uv run askproject doctor --online
   uv run askproject sync                    # DB를 안 옮겼을 때
   uv run askproject host on                 # 봇·콘솔·백업 launchd 등록
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.askproject.tunnel.plist   # plist도 함께 옮겼다면
   bash scripts/build-macos-app.sh        # 이 맥 경로가 박힌 앱으로 새로 빌드·설치
   ```
3. **구독 모드를 쓸 거면** 새 맥에서 `claude auth login`으로 로그인해야 한다(OAuth 자격증명은 맥 로컬 키체인에 있다). API 모드는 `.env`만 옮기면 끝.
4. **주의: 봇은 한 대에서만 켠다.** `serve.lock`은 각 맥의 로컬 파일이라 두 맥이 동시에 봇을 띄우면 슬랙이 이벤트를 양쪽에 나눠 줘 답이 엇갈리고 감사 로그가 갈라진다. 옮긴 뒤 예전 맥은 `askproject host off`로 끈다.

## 웹 콘솔 외부 공개 (Cloudflare Tunnel + Access)

콘솔은 여전히 `127.0.0.1:<콘솔 포트>`에만 바인딩하고, 맥북의 `cloudflared`가 나가는 연결로 Cloudflare에 붙는다. Cloudflare Access가 터널 앞에서 이메일 인증을 거므로 허용된 사람만 열린다.

**콘솔 포트**: 기본 `18787`(`8787`은 `wrangler dev` 등 개발 서버와 자주 겹쳐 옮겼다). `ASKPROJECT_CONSOLE_PORT`로 고정할 수 있다. 팀 공유가 꺼져 있으면 포트가 사용 중일 때 다음 빈 포트(최대 20개)로 옮기고, 앱·`status --json`은 `data/console.json`의 실제 포트를 따라간다. 팀 공유를 켜면 대시보드 Service URL과 같아야 하므로 옮기지 않는다 — 앱에서 팀 공유를 처음 저장할 때 그 포트를 `ASKPROJECT_CONSOLE_PORT`로 고정하고, 포트를 정하지 않고 이미 팀 공유를 쓰던 설치는 예전 포트 `8787`을 그대로 쓴다. 옮기려면 `ASKPROJECT_CONSOLE_PORT=18787`을 넣고 대시보드 Service URL을 같이 바꾼 뒤 콘솔을 다시 켠다. 포트가 겹치면 메뉴 막대 앱에 '포트를 다른 프로그램이 사용 중'이 뜬다(콘솔의 `X-AskProject-Console` 헤더로 구별).

**하지 말 것**: `0.0.0.0` 바인딩(같은 네트워크 전체에 감사 로그가 열린다), `cloudflared tunnel --url`(trycloudflare.com 임시 주소 — 주소만 알면 누구나 연다). 콘솔에는 슬랙 질문 원문이 영구 보존되므로, Allow 정책은 운영자 몇 명으로 좁히는 것을 기본으로 한다.

### 배포 앱 설치자용 (설정 → 팀 공유)

터미널 없이 대시보드와 앱 화면만 쓴다. `cloudflared`는 앱에 들어 있다(Cloudflare 서명 유지, `--no-autoupdate`).

1. Cloudflare에 도메인을 연결해 둔다(필수). Zero Trust는 50명까지 무료.
2. **Networking → Tunnels → Create a tunnel**(Cloudflared) — [바로 가기](https://dash.cloudflare.com/?to=/:account/tunnels). 설치 명령은 실행하지 않고, 명령 전체(또는 `eyJ…` 토큰)를 앱의 **터널 토큰**에 붙여 넣고 **저장하고 터널 켜기**. 대시보드는 터널이 연결돼야 **Continue**가 켜진다 — 그래서 터널은 토큰만으로 뜬다(Access 값이 없는 동안 콘솔은 공개 주소 요청을 421·403으로 거절).
3. **Continue** → **Published application**(이미 만든 터널이면 터널 → **Routes** 탭 → **Add route**): `askproject.<도메인>`, Service URL은 앱 화면에 보이는 주소(기본 `http://127.0.0.1:18787`).
4. **Zero Trust → Access controls → Applications → Create new application → Self-hosted** — [바로 가기](https://dash.cloudflare.com/?to=/:account/one/access-controls/apps). 같은 주소로 만들고 Allow 정책에 팀원 이메일을 넣는다. 만든 앱의 **Additional settings 탭 → AUD tag**를 앱에 붙여 넣는다.
5. 앱의 **공개 주소에서 찾기**로 팀 도메인을 채운다(`setup team-domain --host …`가 Access 로그인 리다이렉트 `https://<팀>.cloudflareaccess.com/…`에서 읽는다). 직접 넣으려면 Zero Trust → Overview → Account details → Team domain. → **저장** → **팀 공유 켜기** → **연결 확인**.

앱 화면은 단계마다 위 바로 가기·문서 링크와 그 단계 값의 입력 칸을 붙여 둔다(`TeamShareView.swift`, 설정 → 팀 공유와 마법사 마지막 단계). 대시보드 링크는 `?to=/:account/…` 형식(대시보드가 현재 계정으로 바꿔 줌)이고, 터널 토큰을 넣으면 토큰의 계정·터널 ID로 그 터널의 Routes·Access 앱 화면을 바로 연다. 경로는 `CloudflareDash`(`TeamShareView.swift`) 한곳에 있다. 대시보드 개편 때 경로가 바뀌므로(예: 문서의 `one/access/apps`는 2026-10 기준 404, 현재 `one/access-controls/apps`) 깨지면 거기만 고친다.

| 항목 | 값 |
| --- | --- |
| 저장 위치 | 주소·팀 도메인·AUD는 `settings.env`(`config set`), 터널 토큰은 Keychain `ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN` |
| 상시 실행 | SMAppService 에이전트 `com.askproject.tunnel`(`askproject tunnel`). 봇 켜기·끄기와 따로 움직이고, 토큰이 없으면 띄우지 않고 끝난다(나머지 값은 없어도 뜬다) |
| 격리 | 빈 `--config`(데이터 폴더의 `cloudflared.yml`)를 줘서 `~/.cloudflared/config.yml`을 읽지 않는다. 토큰은 인자가 아니라 `TUNNEL_TOKEN` 환경변수(다른 환경변수는 넘기지 않는다 — cloudflared가 시작 로그에 환경변수를 찍고 `TUNNEL_TOKEN`만 가린다) |
| 연결 확인 | `askproject setup check tunnel`: 빈 항목 → 토큰 형식 → cloudflared → 이 터널의 `/ready` 연결 수(실행마다 빈 포트를 골라 `data/tunnel.json`에 적는다 — 따로 띄운 cloudflared와 섞이지 않게) → Access 팀 키 → 공개 주소가 Access 로그인으로 넘어가는지(넘겨 주는 팀 도메인이 저장 값과 같은지도). 로그인 없이 응답하면(Access 앱 누락) 실패로 알린다 |
| 로그 | `~/Library/Logs/AskProject/tunnel.log` |
| 끄기 | 앱에서 **팀 공유 켜기**를 끈다. 완전 철거는 대시보드에서 터널·Access 앱 삭제 |
| 앱 종료 | 메뉴 막대 앱을 끄면 터널을 내린다(`applicationWillTerminate`, 켜 둔 설정은 남김). 앱을 다시 켜면 띄운다. 봇·콘솔·백업은 앱과 상관없이 계속 돈다 |

### 개발 앱 (스위치)

개발 앱도 설정 → 팀 공유의 **팀 공유 켜기** 스위치로 켜고 끈다. 스위치는 `askproject host tunnel-on|tunnel-off`를 부르고, 이 명령이 `~/Library/LaunchAgents/com.askproject.tunnel.plist`(`uv run askproject tunnel`, 로그 `data/logs/tunnel.log`)를 쓰고 지운다. 토큰은 `.env`의 `ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN`.

- 배포 앱과 레이블(`com.askproject.tunnel`)이 같다. `tunnel-on`은 같은 레이블로 떠 있는 터널(배포 앱이 띄운 것 포함)을 먼저 내린다 — 한 맥에 터널이 둘이면 Cloudflare가 요청을 나눠 보낸다. 배포 앱에서도 팀 공유를 켜 뒀다면 다음 로그인 때 둘 중 먼저 뜬 쪽이 이기므로, 한쪽 앱에서만 켜 둔다.
- 연결 확인은 자기 `data/tunnel.json`이 없으면 다른 모드의 상태 파일(`~/Library/Application Support/AskProject/data/`, 등록된 plist의 레포 `data/`)도 본다.
- 앱 종료 때 `tunnel-pause`(plist는 둠), 켤 때 `tunnel-resume`.

### 개발 모드 (CLI 방식, 예전 방식)

자격증명 파일 방식으로 직접 띄울 때. 대시보드 토큰 방식은 위 스위치를 쓴다.

| 항목 | 값 |
| --- | --- |
| 공개 주소 | `https://askproject.<내 도메인>` (예: Cloudflare에 등록된 도메인의 서브도메인) |
| 터널 | `cloudflared tunnel create askproject-console` → `~/.cloudflared/config.yml`에 `askproject.<도메인>: http://127.0.0.1:<콘솔 포트>` ingress(`ASKPROJECT_CONSOLE_PORT`로 고정), 자격증명 `~/.cloudflared/*.json` |
| 상시 실행 | launchd `com.askproject.tunnel` (`~/Library/LaunchAgents/`), 로그 `data/logs/tunnel.log` |
| 인증 | Zero Trust → Access controls → Applications의 `AskProject 콘솔` 앱, Allow 정책(이메일 목록) |
| 콘솔 쪽 검증 | `.env`에 `ASKPROJECT_CONSOLE_PUBLIC_HOST=askproject.<도메인>`, `ASKPROJECT_CF_ACCESS_TEAM_DOMAIN=<팀>.cloudflareaccess.com`, `ASKPROJECT_CF_ACCESS_AUD=<Access 앱의 AUD 태그>`. 공개 호스트로 온 요청은 Access JWT(`Cf-Access-Jwt-Assertion`)를 팀 키로 검증한다. 셋이 없으면 공개 요청을 막는다(로컬은 실행당 토큰) |
| 팀원 추가 | 그 정책의 Emails에 주소를 추가하면 됨(코드 변경 없음) |
| 끄기 | `launchctl bootout gui/$(id -u)/com.askproject.tunnel` + plist 삭제. 완전 철거는 `cloudflared tunnel delete askproject-console` + Access 앱 삭제 |

## 언제 SQLite를 벗어나나

서버 1대, 팀 하나, 하루 수십 건인 파일럿에는 SQLite로 충분하다. 쓰는 곳은 봇 하나이고 콘솔은 읽기만 한다. 아래 중 하나가 생기면 PostgreSQL을 검토한다.

- 봇을 여러 대 띄워야 할 때
- 여러 팀이 같은 DB를 쓸 때
- 관리형 백업·시점 복구·접근 권한 관리가 필요할 때

## 크기 예상

- 감사 로그: 질문 1건에 수십 KB(답변·탐색 과정·근거). 하루 30건이면 월 수 MB.
- 커밋 스냅숏: 코드가 바뀐 동기화마다 약 3,600행. `maintenance`가 답변이 참조하지 않는 커밋을 지운다.
- 2026-10-16 기준: DB 21MB(질문 115건, 인덱스 포함), 클론 84MB.
