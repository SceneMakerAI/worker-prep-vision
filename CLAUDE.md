# worker-prep-vision — SceneMaker 영상 전처리 워커

원본 영상을 scenedetect 로 동적 분할하고 세그먼트별 대표 프레임을 사전추출해 t_segment 에
사전등록하는 FastAPI 워커. 구조·관례는 형제 모듈 `agent-vision` 을 계승한다.

## 문서 안내 — 자세한 내용은 필요할 때 해당 문서를 읽을 것

| 문서 | 언제 읽나 |
|------|----------|
| [README.md](README.md) | 서비스 개요·API 스펙·설치·배포 절차 |
| [.aidoc/architecture.md](.aidoc/architecture.md) | 계층 구조·처리 흐름·DB 계약·상태코드·동시성 모델 |
| [.aidoc/detection.md](.aidoc/detection.md) | 분할 알고리즘·파라미터 의미와 튜닝·병렬 탐지 설계·품질 검증 이력 |
| [.aidoc/operations.md](.aidoc/operations.md) | 배포·update.sh·로그·트러블슈팅·성능 기준치 |

이 파일에는 **규칙만** 둔다. 구현 상세를 여기 늘리지 말고 위 문서에 쓸 것.

## 코딩 규칙

- **`src/` 가 import 루트**(`PYTHONPATH=src`, 비패키지 앱). import 는 `from prep...`·
  `from api...`·`from persistence...`·`from config import ...` — `from src...` 아님.
- **계층 경계 준수**: HTTP=`api/`, 미디어 처리=`prep/`, DB=`persistence/`(통로 `db.py`,
  쿼리 `*Repo`). 계층 침범 코드 금지.
- 블로킹 작업(scenedetect·ffmpeg)은 반드시 `asyncio.to_thread` 로 오프로드 — 이벤트 루프를
  막는 코드 금지.
- docstring 은 한국어 Summary/Args/Returns/Description. 린트 ruff(line-length 100).
- **주석 위생**: 코드 되풀이 주석·날짜 스탬프 금지. 남기는 주석은 *왜*(설계 근거·함정)만.
- 병렬 탐지 워커 함수는 spawn 으로 실행된다 — 모듈 최상위에 두고, detect 를 호출하는
  스크립트에는 `if __name__ == "__main__":` 가드 필수.

## 공통 규칙 (설계 불변식)

- **설정은 전부 `config.Settings`(.env) 경유 — 하드코딩 금지.** 배포별 값은 기본값 없이
  필수(fail-fast). 시간 관련 설정은 프레임 수가 아니라 **초 단위**로 통일한다.
- **DB=SSOT**: 세그먼트 목록의 진실원천은 t_segment(파일시스템 아님). 이 워커는 t_segment
  를 **생성만** 한다 — 분석 결과 write 금지(하류 몫).
- 원본 파일명은 API 요청(`file_name`)으로 받는다 — 설정·소스에 파일명 하드코딩 금지.
- 산출 프레임 경로 규칙 `{frames_root}/{v_id}/seg{id:05d}/f{i:03d}.jpg` 은 하류(agent-vision)
  와의 계약 — 임의 변경 금지.

## 보안 규칙

- **`.env` 절대 커밋 금지**(gitignore 확인). 추적 파일(.env.example·문서·유닛·스크립트)에는
  IP·포트·계정·비밀번호·내부 서버명(호스트명)을 쓰지 않는다 — placeholder(`<db-host>`)만.
- 외부 입력이 **파일 경로에 닿으면 반드시 검증**: `file_name` 은 경로 구분자·`..`·절대경로
  차단(PrepRequest validator). 새 입력 필드를 추가할 때도 동일 원칙.
- SQL 은 **파라미터 바인딩(%s)만** — f-string/format/문자열 연결로 SQL 조립 금지.
- subprocess 는 리스트 argv 만 — `shell=True` 금지.
- 시크릿은 로그·응답에 노출 금지(`Settings.__str__` 의 db_pw 마스킹 유지). 500 응답에
  스택트레이스 미노출(전역 핸들러) 유지.
- 이 저장소는 **공개 레포** — 커밋 전 내부 정보(서버명·IP·운영 수치의 식별 가능 정보) 여부를
  항상 점검한다. 권한 구성(sudo 정책 등) 서술도 문서에 남기지 않는다.

## 실행

```bash
uv sync && cp .env.example .env      # .env 채우기
PYTHONPATH=src uv run python src/run.py
# 헬스: /healthz /readyz · 전처리: POST /api/v1/prep {"v_id":1010,"file_name":"source.mp4","force":true}
# 린트: uv run ruff check src
```
