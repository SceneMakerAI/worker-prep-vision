# 아키텍처

## 계층 구조

`src/` 가 import 루트(`PYTHONPATH=src`, 비패키지 앱 — `[tool.uv] package=false`).

```
src/
├─ run.py            # 엔트리포인트 — .env 의 APP_HOST/APP_PORT 로 uvicorn 기동
├─ app.py            # 앱 조립 — lifespan(DB 풀 수명)·라우터 등록·전역 예외 핸들러
├─ config.py         # Settings(.env) — 모든 설정의 단일 관문. 하드코딩 금지
├─ log.py            # 로깅 — stdout + (LOG_PATH 시) 회전 파일 10MB×5
├─ api/              # HTTP 경계 — 검증·상태코드·에러 본문만. 도메인 로직 없음
│  ├─ router.py      #   /api/v1 집계
│  ├─ health.py      #   /healthz(생존) · /readyz(DB+ffmpeg, 미준비 503)
│  ├─ prep.py        #   POST /prep/segment(202) · GET /prep/segment/{v_id} (구경로 별칭 유지)
│  └─ board.py       #   POST /board/analyze(202) · GET /board/analyze/{v_id}
├─ prep/             # 전처리 — CPU 블로킹은 전부 asyncio.to_thread 로 오프로드
│  ├─ detect.py      #   scenedetect 분할 + 밀리초 스냅 타일 후처리(+ 청크 병렬)
│  ├─ frames.py      #   ffmpeg 프레임 추출(스레드풀 병렬)
│  └─ pipeline.py    #   오케스트레이션: 원본확인→분할→추출→등록→상태갱신
├─ board/            # 보드 분석 — 크롭(앞단 산출물) → 상태 타임라인
│  ├─ dedup.py       #   시간축 그룹핑(MAE 경계·잡음 필터·공백 구간 분리)
│  ├─ reader.py      #   VLM 판독(kind별 프롬프트, 스레드풀 병렬, vote_k 다수결)
│  └─ pipeline.py    #   오케스트레이션: 크롭확인→그룹핑→판독→t_board_state 등록
└─ persistence/      # DB 계층 — 통로(db.py)와 쿼리(*Repo) 분리
   ├─ db.py          #   asyncmy 커넥션 풀 래퍼(도메인 모름)
   ├─ videos.py      #   t_video 조회·상태 갱신(상태 갱신은 prep 전용)
   ├─ segments.py    #   t_segment 사전등록·삭제·집계
   └─ board_states.py#   t_board_state 등록·삭제·집계
```

- import 는 `from prep...`·`from api...`·`from persistence...`·`from config import ...`
  형태 — `from src...` 아님.
- 계층 침범 금지: api 가 ffmpeg 을 알거나 prep 이 SQL 을 만지는 코드는 두지 않는다.

## 처리 흐름 (pipeline.run_prep)

```
1. 원본 확인    settings.source_path(v_id, file_name) 부재 → t_video=-1, 종료
2. 분할        detect_windows() → [(seg_id, start, end)] 밀리초 스냅 타일 (to_thread)
3. 프레임 추출  extract_frames() → {frames_root}/{v_id}/seg{id:05d}/f{i:03d}.jpg (to_thread)
4. 사전등록    force 면 기존 삭제 후 t_segment INSERT (status 2001)
5. 상태 갱신   t_video → 1002 (전처리 완료)
```

- 202 접수와 실제 작업이 분리돼 있다 — 물리 검증(원본 존재 등)은 백그라운드에서 하고,
  API 단계에서는 DB 사전조건(404/409)만 검사한다.
- **주의**: force 재실행이 원본 부재로 실패하면 기존 세그먼트는 남고 status 만 -1 이 된다
  (삭제가 분할·추출 성공 뒤에 실행되는 순서라 실패 시 데이터를 날리지 않음).

## 데이터 계약

### 산출 프레임 경로 (하류 정합)

```
{FRAME_ROOT|VOD_ROOT}/{v_id}/seg{seg_id:05d}/f{idx:03d}.jpg
```

agent-vision 의 `frame_paths`(image 모드 file:// 입력) 규칙과 일치해야 한다.
agent-vision 은 이 경로를 **읽기만** 한다.

### DB (SSOT)

- **t_video**: `v_id, cate_id, name, segment_sec, dir, status_code` — duration 컬럼 없음
  (영상 길이는 scenedetect 가 원본에서 직접 읽음).
- **t_segment**: PK (v_id, seg_id). 이 워커는 `start_time/end_time(SEC_TO_TIME),
  frame_cnt, status_code=2001, status_reason='PENDING'` 으로 **생성만** 한다. 분석 결과
  컬럼(summary·replay 등)은 하류 몫.
- **start_time/end_time**(TIME(3)): 밀리초 정밀 세그 경계. 워커가 소수 초를 그대로
  SEC_TO_TIME 에 바인딩해 저장한다. 실질 정밀도는 컷의 프레임 간격(30fps ≈ 33ms) 단위.
- **frame_cnt**(SMALLINT UNSIGNED NULL): 해당 세그에 **실제 추출 성공한** 프레임(jpg) 수.
  추출 결과의 부산물이라 등록 INSERT 에 포함(사후 UPDATE 아님 — '생성만' 계약 유지).
  하류는 디렉토리 나열 없이 f{000..N-1}.jpg 존재를 이 값으로 신뢰할 수 있다
  (실패 프레임이 있으면 계획 수보다 작을 수 있음).
- 재요청 가드(409)·force 선행을 전제로 plain INSERT — PK 중복은 레이스/버그 신호이므로
  예외로 터뜨린다(삼키지 않음).
- **t_board_state**: PK (v_id, kind, board_id). board 파이프라인이 생성 — 같은 보드 상태가
  유지된 시간 구간 하나가 한 행(start/end TIME(3)·crop_cnt·value JSON). 크롭 공백으로
  갈라진 구간은 별도 행 — "행 없는 시간대 = 보드 부재"가 하류의 시간 조인 신호.
  t_video 상태는 갱신하지 않음(선형 파이프라인 소유). 스키마 초안 deploy/t_board_state.sql.
- **board 입력 계약**: {vod_root}/{v_id}/crops/{kind}/{초:05d}.jpg — 앞단(스코어보드 검출
  파이프라인)이 생성, 이 워커는 읽기 전용.

### 상태코드 (t_code)

| 대상 | 코드 | 의미 |
|------|------|------|
| t_video | 1001 | 전처리 입력(상류가 세팅) |
| t_video | **1002** | 전처리 완료 — 이 워커의 성공 종착 |
| t_video | 1005/1006 | 대사(STT) 입력/완료 — 이후 하류 분석 |
| t_video | -1 | 실패(원본 없음·분할 결과 없음) |
| t_segment | **2001** | 장면 입력(분석 대기) — 이 워커가 등록하는 상태 |

## 동시성 모델

- FastAPI 이벤트 루프는 절대 블로킹하지 않는다: scenedetect·ffmpeg 은 `asyncio.to_thread`.
- 분할 병렬(`PREP_DETECT_WORKERS`)은 **프로세스** 병렬(spawn — opencv fork 비안전 회피).
- 프레임 추출 병렬(`PREP_CONCURRENCY`)은 **스레드** 풀 — 각 스레드는 ffmpeg subprocess 를
  기다릴 뿐이라 GIL 영향 없음.
- DB 는 앱 수명 동안 커넥션 풀 1개 공유(`app.state.db`), autocommit.
