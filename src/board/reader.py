"""VLM 판독 — 그룹 표본 크롭을 vLLM(OpenAI 호환)에 보내 저장값(txt)으로 바꾼다.

프롬프트는 board.prompts(레퍼런스 검증본) — 시스템 메시지로 넣고 이미지는 유저 턴.
표본 k장을 각각 판독해 후처리(postprocess)까지 마친 값으로 다수결한다 — 원문이 아니라
저장값 기준으로 비교해야 BASE 3줄 블록의 사소한 표기 차이가 표를 가르지 않는다.
동기 urllib + 스레드풀 — 호출자(pipeline)가 asyncio.to_thread 로 감싼다.
"""

import base64
import json
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from board import postprocess
from board.prompts import PROMPTS
from config import Settings
from log import get_logger

log = get_logger(__name__)

# temperature 0: 같은 이미지에 같은 답 — 재실행·검증·다수결이 성립한다.
# max_tokens 128: txt 가 varchar(128) 이라 응답이 그 안에 들어와야 한다.
_SAMPLING = {"temperature": 0, "max_tokens": 128}


class ReadFailure(Exception):
    """표본 판독 실패(재시도 소진) — 호출자가 건별 격리한다(해당 그룹 txt 만 비어 남음)."""


def _ask_one(kind: str, path: Path, settings: Settings) -> str:
    """크롭 1장 판독 — 실패 시 1회 재시도, 소진하면 ReadFailure. 응답 원문(strip)을 반환."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = json.dumps({
        "model": settings.vlm_model,
        **_SAMPLING,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": PROMPTS[kind]}]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]},
        ],
    }).encode()
    url = settings.vlm_url.rstrip("/") + "/v1/chat/completions"
    err = "unknown"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=settings.vlm_timeout) as r:
                content = json.loads(r.read())["choices"][0]["message"]["content"] or ""
            return content.strip()
        except Exception as e:  # 타임아웃·연결 실패 — 재시도 후 격리용 예외로 승격
            err = str(e)[:120]
    raise ReadFailure(f"{kind} {path.name}: {err}")


def read_group(kind: str, samples: list[Path], settings: Settings) -> tuple[str, bool]:
    """
    Summary:
        그룹 표본 k장을 판독·후처리하고 다수결로 (저장할 txt, 후처리 성공 여부)를 정한다.
    Args:
        kind (str): 판독 항목(대문자). samples (list[Path]): 시작/중간/끝 등 표본 경로.
        settings (Settings): VLM 엔드포인트.
    Returns:
        tuple[str, bool]: 다수결 저장값과 후처리 성공 여부(실패면 원문 저장됨).
    Raises:
        ReadFailure: 표본 전체가 판독 실패한 경우(부분 실패는 남은 표본으로 다수결).
    """
    values: list[tuple[str, bool]] = []
    fail: ReadFailure | None = None
    for p in samples:
        try:
            values.append(postprocess.apply(kind, _ask_one(kind, p, settings)))
        except ReadFailure as e:   # 부분 실패 격리 — 남은 표본으로 진행
            fail = e
    if not values:
        raise fail if fail else ReadFailure(f"{kind}: 표본 없음")
    counted = Counter(v for v, _ in values)
    top, top_n = counted.most_common(1)[0]
    if top_n > 1:
        return top, next(ok for v, ok in values if v == top)
    return values[len(values) // 2]        # 전원 불일치 — 중간 표본(전환 오염 최소) 채택


def read_groups(jobs: list[tuple[str, list[Path]]], settings: Settings) -> list:
    """
    Summary:
        (kind, 표본들) 목록을 스레드풀 병렬로 판독해 같은 순서의 결과 목록을 돌려준다.
    Args:
        jobs: 그룹당 하나 — kind 와 판독 표본 경로들.
        settings (Settings): VLM 엔드포인트·동시성.
    Returns:
        list[tuple[str, bool] | ReadFailure]: jobs 순서대로 (txt, 후처리성공) 또는 실패.
    Description:
        - 블로킹(HTTP×N) — 호출자가 asyncio.to_thread 로 감싼다.
        - 실패는 예외 대신 값으로 돌려준다 — 한 그룹 실패가 배치를 못 죽이게(건별 격리).
    """
    def one(job):
        try:
            return read_group(job[0], job[1], settings)
        except ReadFailure as e:
            return e

    with ThreadPoolExecutor(max_workers=settings.board_read_concurrency) as ex:
        return list(ex.map(one, jobs))
