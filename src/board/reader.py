"""VLM 판독 — 그룹 대표 크롭을 vLLM(OpenAI 호환)에 보내 구조화 값(JSON)으로 바꾼다.

프롬프트 6종은 4경기 2,785건 판독 실패 0 으로 검증된 문구 — 임의 수정 주의.
동기 urllib + 스레드풀(검증된 호출 패턴 그대로) — 호출자(pipeline)가 asyncio.to_thread 로 감싼다.
"""

import base64
import json
import re
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import Settings
from log import get_logger

log = get_logger(__name__)

# kind별 판독 프롬프트 — 값은 JSON 만 출력하도록 강제(파싱 안정성)
PROMPTS = {
    "inning": '야구 스코어보드 이닝 크롭. ▲=초, ▼=말. JSON {"inning": n, "half": "초|말"} 만 출력.',
    "out": '야구 스코어보드 아웃카운트 크롭. 빨갛게 켜진 원 개수가 아웃 수(0~2). JSON {"out": n} 만 출력.',
    "base": (
        "야구 스코어보드 주자 크롭. 다이아몬드 3개: 위=2루, 왼쪽아래=3루, 오른쪽아래=1루. "
        '주황/빨강으로 켜진 베이스=주자 있음. JSON {"base1": 0, "base2": 0, "base3": 0} 형식으로만 출력.'
    ),
    "count": '야구 스코어보드 볼-스트라이크 크롭. B-S 형식 숫자. JSON {"ball": n, "strike": n} 만 출력.',
    "team": (
        '야구 스코어보드 팀/점수 크롭. JSON {"left": "팀명", "left_score": n, '
        '"right": "팀명", "right_score": n} 만 출력. 안 보이는 값은 null.'
    ),
    "etc": '야구 중계 정보 스트립(투수 투구수/타자 타율 등). 보이는 핵심 정보를 JSON {"text": "요약"} 으로만 출력.',
}


def _ask_one(kind: str, path: Path, settings: Settings) -> dict:
    """크롭 1장 판독 — 실패 시 1회 재시도, 그래도 실패면 {"error": ...} 반환(파이프라인 계속)."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = json.dumps({
        "model": settings.vlm_model,
        "max_tokens": settings.vlm_max_tokens,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text", "text": PROMPTS[kind]},
        ]}],
    }).encode()
    url = settings.vlm_url.rstrip("/") + "/v1/chat/completions"
    err = "unknown"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=settings.vlm_timeout) as r:
                content = json.loads(r.read())["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", content, re.S)
            return json.loads(m.group()) if m else {"raw": content.strip()[:200]}
        except Exception as e:  # 타임아웃·연결 실패·JSON 파싱 실패 — 재시도 후 error 로 기록
            err = str(e)[:120]
    log.warning("VLM 판독 실패: kind=%s file=%s (%s)", kind, path.name, err)
    return {"error": err}


def _ask_vote(kind: str, paths: list[Path], settings: Settings) -> dict:
    """표본 k장 판독 후 다수결 — 동률이면 중간 표본 값. k=1 이면 단일 판독과 동일."""
    values = [_ask_one(kind, p, settings) for p in paths]
    if len(values) == 1:
        return values[0]
    counted = Counter(json.dumps(v, ensure_ascii=False, sort_keys=True) for v in values)
    top, top_n = counted.most_common(1)[0]
    return json.loads(top) if top_n > 1 else values[len(values) // 2]


def read_groups(jobs: list[tuple[str, list[Path]]], settings: Settings) -> list[dict]:
    """
    Summary:
        (kind, 표본 경로들) 목록을 스레드풀 병렬로 판독해 같은 순서의 값 목록을 돌려준다.
    Args:
        jobs (list[tuple[str, list[Path]]]): 그룹당 하나 — kind 와 판독 표본 경로들.
        settings (Settings): VLM 엔드포인트·동시성.
    Returns:
        list[dict]: jobs 와 같은 순서의 판독 값(실패 시 {"error": ...}).
    Description:
        - 블로킹(HTTP×N) — 호출자가 asyncio.to_thread 로 감싼다.
    """
    with ThreadPoolExecutor(max_workers=settings.board_read_concurrency) as ex:
        return list(ex.map(lambda j: _ask_vote(j[0], j[1], settings), jobs))
