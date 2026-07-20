# worker-prep-vision

SceneMaker **영상 전처리 워커** — 원본 영상을 scenedetect 로 **동적 분할**하고, 세그먼트별
**대표 프레임(jpg)을 사전추출**한 뒤 **t_segment 에 사전등록**하는 FastAPI 서비스.
후속 분석(agent-vision)이 곧바로 돌 수 있는 상태로 영상(v_id)을 준비한다.

```
POST /api/v1/prep {v_id, file_name, force}  →  202 (즉시 접수)
  └ background: 원본 확인 → scenedetect 동적 분할 → 프레임 추출(ffmpeg)
                → t_segment 사전등록 → t_video 상태 갱신
```

- **분할**: 고정 그리드가 아니라 콘텐츠 변화 기반 동적 경계. 정수 초 스냅, 최소/최대 길이
  병합·분할로 틈·겹침 없는 시간 타일을 만든다. 시간축 청크 병렬 탐지 지원(멀티코어).
- **프레임**: 세그당 `max(1, round(길이×PREP_FPS))`장 — 각 소구간의 중앙 시각에서 추출
  (경계 전환/블랙 프레임 회피). ffmpeg 프로세스 병렬.
- **DB = 단일 진실원천**: 세그먼트 목록은 파일시스템이 아니라 t_segment. 이 워커는 생성만
  담당하고 분석 결과 기록은 하류 모듈 몫이다.

## 요구사항

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- ffmpeg / ffprobe (PATH 에 존재)
- MariaDB (t_video·t_segment 스키마)
- 원본 영상이 로컬 경로(`{VOD_ROOT}/{v_id}/{file_name}`)에 존재

## 시작하기

```bash
git clone https://github.com/SceneMakerAI/worker-prep-vision.git
cd worker-prep-vision
uv sync
cp .env.example .env        # 실제 값 채우기 (DB 접속정보·경로·포트)
PYTHONPATH=src uv run python src/run.py
```

헬스 체크:

```bash
curl http://127.0.0.1:<APP_PORT>/healthz   # 프로세스 생존
curl http://127.0.0.1:<APP_PORT>/readyz    # DB + ffmpeg 준비 (미준비 시 503)
```

## API

### `POST /api/v1/prep` — 전처리 접수

```bash
curl -X POST http://127.0.0.1:<APP_PORT>/api/v1/prep \
  -H "Content-Type: application/json" \
  -d '{"v_id": 1010, "file_name": "source.mp4", "force": false}'
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `v_id` | int | 대상 영상 id (t_video 에 존재해야 함) |
| `file_name` | str | `{VOD_ROOT}/{v_id}/` 아래 원본 파일명. **경로 구분자·상위참조 불가** |
| `force` | bool | 기존 세그먼트 삭제 후 재전처리 (기본 false) |

응답 `202`: `{"v_id": 1010, "accepted": true, "source": "<원본 경로>"}` — 실제 작업은 백그라운드.

| 오류 | 코드 | 의미 |
|------|------|------|
| 404 | `VIDEO_NOT_FOUND` | t_video 에 v_id 없음 |
| 409 | `ALREADY_PREPPED` | 이미 세그먼트 존재 — 재실행은 `force=true` |
| 422 | — | `file_name` 검증 실패(경로 탈출 시도 등) |

### `GET /api/v1/prep/{v_id}` — 진행 상태

```bash
curl http://127.0.0.1:<APP_PORT>/api/v1/prep/1010
# {"v_id": 1010, "status": 1002, "segments": 1678}
```

`status`: `1001` 전처리 입력 → `1002` 전처리 완료 / `-1` 실패(원본 없음·분할 결과 없음).

## 설정 (.env)

전체 항목과 기본값은 [.env.example](.env.example) 참조. 주요 항목:

| 항목 | 의미 |
|------|------|
| `APP_HOST` / `APP_PORT` | API 바인딩 |
| `VOD_ROOT` | 원본·프레임 루트. 원본 = `{VOD_ROOT}/{v_id}/{file_name}` |
| `PREP_THRESHOLD` | 장면 전환 감도(낮을수록 컷 촘촘) |
| `PREP_MIN_SEC` / `PREP_MAX_SEC` | 세그 최소(미만 병합)·최대(초과 균등분할) 길이 |
| `PREP_DETECT_WORKERS` | 분할 병렬 프로세스 수(0=단일 패스). 멀티코어 가속 |
| `PREP_FPS` | 세그당 추출 프레임 밀도(초당 장수) |
| `PREP_CONCURRENCY` | 프레임 추출 ffmpeg 동시 실행 수 |
| `DB_*` | MariaDB 접속 정보 |

분할 파라미터의 의미와 튜닝 가이드는 [.aidoc/detection.md](.aidoc/detection.md) 참조.

## 배포

### 최초 1회 (systemd)

```bash
# 1) 배포 위치에 clone 후 .env 작성, uv sync
# 2) systemd 유닛 설치 (deploy/prep-vision.service 가 정본)
sudo install -m644 deploy/prep-vision.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prep-vision.service
```

### 패치 배포 (자체 업데이트)

main 브랜치에 push 한 뒤 배포 서버에서:

```bash
deploy/update.sh            # 변경 없으면 no-op
deploy/update.sh --force    # 강제 재배포
```

`git fetch → reset --hard origin/main → uv sync --frozen → 유닛 동기화 → 재시작 → readyz 대기`
순서로 진행되며, `.env` 는 git 미추적이라 보존된다. 상세는
[.aidoc/operations.md](.aidoc/operations.md) 참조.

## 성능 참고치

48 vCPU 서버, 115분(25fps) 실영상 기준:

| 단계 | 소요 |
|------|------|
| 장면 분할 (병렬 12워커) | 13초 |
| 프레임 추출 약 2,000장 (동시성 42) | 14초 |
| t_segment 등록 약 1,700행 | 3초 |
| **전체** | **약 30초** (실시간 대비 ~230배) |

## 문서

- [.aidoc/architecture.md](.aidoc/architecture.md) — 계층 구조·데이터 흐름·상태코드
- [.aidoc/detection.md](.aidoc/detection.md) — 분할 알고리즘·파라미터 튜닝·병렬 설계
- [.aidoc/operations.md](.aidoc/operations.md) — 배포·운영·벤치마크 기록
