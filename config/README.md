# config/

서비스마다 채우는 설정 파일이 여기 놓인다. **git에 넣지 않는다**(`.gitignore`의 `config/*.yaml`) — 대상 레포가 비공개일 수 있어서다.

| 파일 | 내용 | 채우는 방법 |
| --- | --- | --- |
| `repos.yaml` | 대상 레포, 기준 ref, 제외 규칙, `structure:` 구조 맵 규칙 | 직접 쓰거나 앱 설정 → 레포 (`askproject config set`) |
| `domains.yaml` | 기획자 관점 도메인과 도메인별 허용 경로 | `askproject learn domains` 또는 직접 |
| `glossary.yaml` | 기획 용어 → 코드 식별자 힌트 | `askproject learn glossary` 또는 직접 |

파일이 없으면 `askproject` 명령을 처음 실행할 때 `src/askproject/defaults/`의 빈 템플릿을 복사한다. 템플릿을 고칠 때는 `defaults/` 쪽을 고친다.
