-- t_board_state DDL 초안 — 적용 전 검토용(자동 실행 안 함).
--
-- 보드 상태 타임라인: 같은 스코어보드 상태가 유지된 시간 구간 하나가 한 행.
-- 크롭 공백(보드 부재)으로 갈라진 구간은 별도 행 — "행이 없는 시간대 = 보드 부재"가
-- 리플레이/광고 탐지의 조인 신호가 된다.
--
-- 상태코드 컬럼은 두지 않는다(v1): 분석 진행 여부는 행 존재 자체가 나타내고,
-- t_video.status_code 는 전처리·STT 선형 파이프라인 소유라 건드리지 않는다.
-- 별도 진행 상태가 필요해지면 t_code 에 BOARD 객체 코드 신설을 검토(결정 필요).

CREATE TABLE t_board_state (
    v_id         MEDIUMINT UNSIGNED NOT NULL,          -- t_video.v_id
    kind         VARCHAR(20)        NOT NULL,          -- base|count|etc|inning|out|team
    board_id     SMALLINT UNSIGNED  NOT NULL,          -- kind 내 시간순 1부터
    start_time   TIME(3)            NOT NULL,          -- 상태 시작(밀리초)
    end_time     TIME(3)            NOT NULL,          -- 상태 끝(마지막 관측 크롭 시각)
    crop_cnt     SMALLINT UNSIGNED  NOT NULL,          -- 구간을 구성한 원본 크롭 장수
    value        LONGTEXT           NOT NULL CHECK (JSON_VALID(value)),  -- 판독 값(JSON)
    reg_datetime DATETIME           NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (v_id, kind, board_id),
    KEY idx_time (v_id, start_time)                     -- t_segment 와 시간 조인용
) COMMENT='스코어보드 상태 타임라인 — worker-board-vision 이 생성';
