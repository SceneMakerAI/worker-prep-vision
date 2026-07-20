# 운영 — 배포·업데이트·트러블슈팅

## 배포 형태

- 배포 서버의 표준 경로에 **git clone** 으로 존재하며, GitHub `main` 이 코드 정본이다.
- systemd 서비스 `prep-vision.service` 로 상주. **유닛 파일의 정본은 저장소
  `deploy/prep-vision.service`** — 서버의 `/etc/systemd/system` 사본은 update.sh 가
  동기화한다. 서버에서 유닛을 직접 고치지 말 것(다음 배포에서 덮임).
- `.env` 는 git 미추적 — 서버 로컬에만 존재하며 배포/reset 에도 보존된다.
- 유닛에 systemd 샌드박스 적용: `ProtectSystem=full`·`PrivateTmp` 등, 쓰기 가능 경로는
  미디어 루트와 로그 디렉토리로 제한.

## 최초 부트스트랩 (신규 서버)

```bash
# 1) 표준 경로에 clone (또는 기존 rsync 사본을 clone 으로 전환)
git init -b main && git remote add origin <repo-url>
git fetch origin && git reset --hard origin/main

# 2) 환경
cp .env.example .env && vi .env       # 실제 값 기입 (chmod 600 권장)
uv sync

# 3) 서비스
sudo install -m644 deploy/prep-vision.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now prep-vision.service
curl -s http://127.0.0.1:<APP_PORT>/readyz
```

ffmpeg 이 배포판 패키지에 없으면 정적 빌드를 받아 PATH 에 두면 된다(유닛의
`Environment=PATH=...` 와 정합 확인).

## 패치 배포 (update.sh)

개발 머신에서 main push → 배포 서버에서:

```bash
deploy/update.sh            # 원격과 같으면 no-op (재시작 안 함)
deploy/update.sh --force    # 변경 없어도 sync+재시작 강제
```

동작: `git fetch → HEAD 비교 → reset --hard origin/main → uv sync --frozen
→ 유닛 diff 시 갱신+daemon-reload → systemctl restart → readyz 최대 30초 대기`.
실패(무응답) 시 exit 1 — `journalctl -u prep-vision` 확인.

- **주의**: 재시작은 진행 중인 백그라운드 prep 작업을 중단시킨다. 대량 전처리가 도는
  중에는 배포를 피하거나, 완료 후(`GET /prep/{v_id}` 로 확인) 배포할 것.
- 서버 로컬 커밋·수정은 reset --hard 로 사라진다. 서버에서 코드를 고치지 말 것.

## 로그

| 위치 | 내용 |
|------|------|
| `{LOG_PATH}` (예: `<로그루트>/prep_vision.log`) | 앱 로그 — 회전 10MB×5, LOG_PATH 미설정 시 stdout만 |
| `journalctl -u prep-vision` | systemd 캡처 — 앱 로깅 구성 전의 부팅 실패·크래시 포함 |

분할 로그 라인에 적용 파라미터가 찍힌다:
`분할(병렬 12워커): source.mp4 (threshold=15.0, detect_fps=0) → 1678세그(총 6923s)`

## 설정 변경 절차

`.env` 수정 후 `sudo systemctl restart prep-vision.service` — 설정은 부팅 시 1회
로드된다(핫리로드 없음). 코드 기본값 변경이 필요하면 config.py 를 고쳐 정식 배포.

## 트러블슈팅

| 증상 | 확인 |
|------|------|
| readyz `db: down` | DB 접속정보(.env)·네트워크·방화벽. `DB_POOL_RECYCLE` 짧게(NAT 경유) |
| readyz `ffmpeg: missing` | PATH 에 ffmpeg 없음 — 유닛 Environment=PATH 확인 |
| prep 후 status=-1 | 원본 부재(`SOURCE_NOT_FOUND`) 또는 분할 결과 없음(`NO_WINDOWS`) — 앱 로그 확인 |
| 409 반복 | 이미 전처리됨 — 의도된 가드. 재실행은 `force=true` |
| 프레임 디렉토리 권한 오류 | 미디어 루트 소유권/ACL — 서비스 실행 계정이 쓰기 가능해야 함 |
| 서비스가 안 뜸 | `journalctl -u prep-vision -n 50` — .env 필수값 누락 시 pydantic 검증 오류로 부팅 실패(fail-fast 설계) |

## 성능 기준치 (48 vCPU, 115분/431MB 25fps 실영상, 2026-07-20 실측)

| 단계 | 설정 | 소요 |
|------|------|------|
| 분할 단일 패스 | detect_fps=0 | 108.6초 |
| 분할 병렬 | 12워커 | **13.1초** (8.3배) — 24워커 이상은 포화(11초대) |
| 프레임 추출 2,069장 | 동시성 16→42 | 14초→~8초 |
| t_segment 1,678행 등록 | executemany | 3초 |
| **전체 파이프라인** | 운영 설정 | **약 30초** |

- 프레임 추출은 프레임 1장당 ffmpeg 1프로세스(입력 시킹) 구조 — 세그가 수만 단위로
  커지면 세그당 배치 추출로 최적화 여지 있음.
- `PREP_DETECT_FPS` 를 낮춰 가속하는 것은 비권장(결과 왜곡 — detection.md 참조).
  속도는 워커 병렬로 해결할 것.
