# worker-prep-vision — SceneMaker 영상 전처리 워커

원본 영상을 **scenedetect 로 동적 분할**하고, 세그먼트별 **대표 프레임(jpg)을 사전추출**한 뒤
**t_segment 에 사전등록**하는 FastAPI 워커. agent-vision 1차 분석이 곧바로 돌 수 있는 상태로 v_id 를
준비한다. 구조·관례는 형제 모듈 `agent/agent-vision` 을 계승한다.

## 처리 흐름
```
POST /api/v1/prep {v_id, file_name, force}  → 202 (즉시)
  └ background: 원본확인 → scenedetect 분할 → 프레임 추출(ffmpeg CPU) → t_segment 등록(2001)
                → t_video 상태(1002 전처리완료)
이후: STT(대사) 완료 → 상류가 agent-vision 분석 호출(별도 모듈)
```
- **분할=구조앵커 아님, 시간 타일**: 콘텐츠 변화 기반 동적 경계(고정 그리드 아님). 정수 초 스냅·
  min/max 병합분할로 청크명·t_segment 정합.
- **프레임 정책**: 세그당 `max(1, round(길이×PREP_FPS))` 장 — 짧은 세그도 최소 1장, 긴 세그는 fps 비례.
  각 세그를 n등분한 소구간 **중앙 시각**에서 추출(경계 전환/블랙 회피). 해상도 native(추후 scale 옵션).
- **file:// 정합**: 산출 `{frames_root}/{v_id}/seg{id:05d}/f{i:03d}.jpg` 는 agent-vision `frame_paths`
  (image 모드 vLLM file:// 입력) 규칙과 일치 — agent-vision 은 읽기만 한다.
- **오디오 미사용**: 이 워커는 프레임만 다룬다(sounds 레인 없음).

## 배포 전제
- **원본과 같은 호스트에 배포** — 원본(`{VOD_ROOT}/{v_id}/{file_name}`)·프레임을 **로컬 파일**로
  접근(ssh 아님). 원본 파일명은 prep 요청(`file_name`)으로 받고, 원본은 상류가 미리 그 경로에 가져다 둔다.
- ffmpeg 은 **CPU 모드**(nvenc 아님, 현 단계). scenedetect+opencv 로 분할.

## 항상 지킬 핵심 (agent-vision 계승)
- **`src/` 가 import 루트**(`PYTHONPATH=src`, `[tool.uv] package=false` 비패키지 앱).
  import 는 `from prep...`·`from api...`·`from persistence...`·`from config import ...` — `from src...` 아님.
- **설정은 전부 `config.Settings`(.env) 경유 — 하드코딩 금지.** `.env` gitignore, `.env.example` 만 추적.
  IP·접속정보는 추적 파일에 placeholder(`<db-host>`)로만.
- **계층 경계**: 미디어 처리(분할·프레임)=`prep/`, HTTP 경계=`api/`, DB=`persistence/`(통로 `db.py`,
  쿼리 `*Repo`). 블로킹(scenedetect·ffmpeg)은 `asyncio.to_thread` 로 오프로드.
- **DB=SSOT**: 세그먼트 목록은 파일시스템이 아니라 t_segment. 이 워커는 t_segment 를 **생성만**(결과
  write 는 agent-vision 몫).
- docstring 한국어 Summary/Args/Returns/Description, 린트 ruff(line-length 100).
- **주석 위생**: 코드 되풀이 무의미 주석·날짜 스탬프 금지. 남기는 주석은 *왜*(설계 근거·함정)만.

## 실행
```bash
uv sync
PYTHONPATH=src uv run python src/run.py          # .env 의 APP_HOST/APP_PORT 로 서빙
# 헬스: GET /healthz(생존) · /readyz(DB+ffmpeg)
# 전처리: POST /api/v1/prep {"v_id": 1010, "file_name": "source.mp4", "force": true}
```

## 배포·업데이트
- 배포 서버 경로 `/usr/service/source/scenemaker/worker/worker-prep-vision`, systemd
  `prep-vision.service` — **`deploy/prep-vision.service` 가 정본**(서버 유닛은 update.sh 가 동기화).
- **자체 업데이트**: 개발 머신에서 main 에 push → 배포 서버에서 `deploy/update.sh` 실행.
  (fetch → `reset --hard origin/main` → `uv sync --frozen` → 유닛 갱신 → 재시작 → readyz 대기.
  `.env` 는 미추적이라 보존됨. 변경 없으면 no-op, `--force` 로 강제.)
- 최초 1회 부트스트랩: 배포 디렉토리에서 `git init && git remote add origin <repo-url>
  && git fetch origin && git reset --hard origin/main`.

## 상태
- **골격 스캐폴딩 완료.** app/config/log/run + api(router·health·prep) + persistence(db·segments·videos)
  + prep(detect·frames·pipeline). 분할 로직은 agent-vision `onboard_detect` 의 검증본 이식.
- **미확정/다음**:
  - t_video 완료 상태코드(현재 `1002 FFMPEG_DONE` 로 세팅) — 상류 STT/파이프라인 상태머신과 최종 정합 확인 필요.
  - 대량 세그(수천) 프레임 추출 성능(CPU per-frame seek) — 필요 시 세그 1콜 배치추출로 최적화.
  - 실사용 검증(실제 v_id 로 prep→프레임 산출→agent-vision image 모드 분석 파리티).
