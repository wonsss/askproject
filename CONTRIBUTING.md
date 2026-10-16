# 기여하기

이슈와 PR을 환영합니다. 큰 변경은 먼저 이슈로 방향을 맞추면 좋습니다.

## 개발 환경

```bash
git clone https://github.com/wonsss/askproject && cd askproject
uv sync                 # Python 3.12, 의존성 (uv.lock 고정)
brew install ripgrep    # code_search 도구·테스트에 필요
make test               # = uv run pytest
make                    # 자주 쓰는 명령 목록
```

- 테스트는 Claude·Slack·GitHub를 부르지 않습니다(API 비용·토큰 없음). 새 기능에는 테스트를 붙여 주세요.
- `ask`·`eval`은 실제 Claude API 비용이 듭니다. PR에 실측 결과를 붙일 때는 비용도 적어 주세요.
- 맥 앱(SwiftUI)은 `bash scripts/build-macos-app.sh`로 개발 빌드합니다(Xcode 명령줄 도구 필요). 배포 빌드·서명은 [docs/packaging.md](docs/packaging.md).
- 코드 지도와 설계 원칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 지켜야 할 원칙

1. **봇은 모든 소스에 읽기 전용입니다.** 에이전트에 쓰기·셸·외부 전송 도구를 추가하지 않습니다.
2. **새로운 외부 통신을 넣지 않습니다.** 꼭 필요하면 [docs/privacy.md](docs/privacy.md)의 표를 함께 고치고 PR 설명에 적어 주세요. 텔레메트리·분석은 받지 않습니다.
3. **대상 레포의 내부 정보를 커밋하지 않습니다.** 채운 `config/*.yaml`, `eval/golden.yaml`, 실제 답변 스크린샷·코드 경로·커밋 해시·서비스 이름이 해당합니다. 문서·테스트 예시는 가상의 서비스(주문·알림 등)로 씁니다.
4. 비밀값(`.env`, `secrets/`, `data/`)을 커밋하거나 로그·출력에 남기지 않습니다.
5. 감사 로그 스키마는 `store/db.py`의 `MIGRATIONS`에 **추가만** 합니다.

## 커밋·PR

- 커밋은 작업 단위로 나누고, 메시지는 한국어로 `feat:`·`fix:`·`docs:`·`ci:`·`build:` 같은 접두사를 씁니다.
- `make test`가 통과해야 합니다(GitHub Actions `test` 워크플로가 PR마다 돌립니다).
- 버전·릴리스는 메인테이너가 `make release`로 합니다. PR에서 버전을 바꾸지 마세요.

## 이슈를 올릴 때

토큰·API 키·대상 레포 코드·실제 질문 내용은 붙여넣지 마세요. 로그가 필요하면 앱의 **진단 묶음**(토큰 형식은 자동으로 가림)을 열어 레포 이름·채널 ID 같은 회사 정보를 지운 뒤 필요한 부분만 옮겨 주세요.
