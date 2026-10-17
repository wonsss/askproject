# 자주 쓰는 명령 모음. `make` 또는 `make help`로 목록을 본다.
# 릴리스는 버전을 올려 태그를 push하면 CI(.github/workflows/release.yml)가 DMG를 빌드·공증해 Release에 올린다.
# Actions가 안 돌 때는 make release-local이 같은 일을 이 맥에서 한다.

.DEFAULT_GOAL := help
.PHONY: help test doctor sync serve console version release release-minor release-major release-local release-local-current release-check app app-dev dmg

V ?=

help:  ## 이 목록
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-15s %s\n", $$1, $$2}'

test:  ## 테스트
	uv run pytest -q

doctor:  ## 환경·연결 점검
	uv run askproject doctor --online

sync:  ## 레포 받기 + 인덱싱 + 노션·슬랙 수집
	uv run askproject sync

serve:  ## 슬랙 봇 실행
	uv run askproject serve

console:  ## 웹 콘솔 (127.0.0.1:18787)
	uv run askproject console

version:  ## 현재 버전 (네 곳이 같은지 확인)
	@python3 scripts/bump_version.py --check

release:  ## 릴리스 patch (0.1.0→0.1.1). 정확한 버전은 make release V=1.2.3
	bash scripts/release.sh $(if $(V),$(V),patch)

release-minor:  ## 릴리스 minor (0.1.0→0.2.0)
	bash scripts/release.sh minor

release-major:  ## 릴리스 major (0.1.0→1.0.0)
	bash scripts/release.sh major

release-local:  ## 이 맥에서 릴리스 (Actions 없이: 빌드·공증·Release 업로드). V=minor·major·1.2.3
	bash scripts/release-local.sh $(if $(V),$(V),patch)

release-local-current:  ## 태그는 push됐는데 Release가 없을 때(CI 실패) 지금 버전으로 이 맥에서 올리기
	bash scripts/release-local.sh --current

release-check:  ## 이 맥에서 릴리스할 준비 점검 (인증서·공증 프로필·Sparkle 키·gh·테스트)
	bash scripts/release-local.sh --check

app:  ## 배포 앱 빌드 + 서명 (dist/AskProject.app)
	bash scripts/build-app.sh

app-dev:  ## 개발 앱 빌드·설치 (uv run으로 이 레포를 부름)
	bash scripts/build-macos-app.sh

dmg:  ## 이 맥에서 공증 DMG (NOTARY_PROFILE=askproject)
	NOTARY_PROFILE=$${NOTARY_PROFILE:-askproject} bash scripts/build-dmg.sh
