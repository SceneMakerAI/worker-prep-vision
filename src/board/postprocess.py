"""판독 응답 → DB 저장값 변환 — 순수 함수 (agent-vision3 read/postprocess 이식).

프롬프트 출력 형식과 DB 저장 형식을 분리한다. 프롬프트는 정확도에 유리한 형태(BASE 는 요소별
3줄)를 쓰고, txt 에는 하류 board 파서가 읽는 짧은 형태('1루, 2루' / '없음')를 넣는다.

원칙:
- **파싱에 실패하면 원문을 그대로 저장한다.** 조용히 버리면 나중에 원인 추적이 불가능하다.
  실패는 호출자가 로그로 남긴다.
- 등록되지 않은 kind 는 손대지 않는다 — 나머지 5종은 출력이 곧 저장 형식이라
  (`2-0`, `1`, `9회초`, `KIA 8: 삼성 2`) 변환할 게 없다.
"""

import re

BASE_EMPTY = "없음"          # 주자 없음의 저장 표기
_BASE_LABELS = (("first|1st", "1루"), ("second|2nd", "2루"), ("third|3rd", "3루"))


def parse_base(raw: str) -> str | None:
    """
    Summary:
        BASE 판독 응답을 '1루, 2루' 형태로 바꾼다.
    Args:
        raw (str): 모델 응답 원문.
    Returns:
        str | None: '1루, 2루' / '없음'. 형식을 못 알아보면 None(호출자가 원문 저장).
    Description:
        - 받는 형식: `First (Right) : On` 3줄 블록(머리줄·불릿 유무 무관), `1st/2nd/3rd` 라벨,
          이미 한국어인 `1루, 2루`(멱등), `없음`.
        - **세 베이스가 모두 잡혀야 성공으로 본다.** 일부만 읽히면 나머지를 Off 로 단정할 수
          없으므로 실패 처리한다 — 잘못된 값을 넣느니 원문을 남기는 편이 낫다.
    """
    t = (raw or "").strip()
    if not t:
        return None

    # 이미 저장 형식인 경우(재실행·수기 입력) — 멱등 처리
    if t == BASE_EMPTY:
        return BASE_EMPTY
    if re.fullmatch(r"\s*[123]루(\s*,\s*[123]루)*\s*", t):
        return ", ".join(sorted({m + "루" for m in re.findall(r"([123])루", t)}))

    on: list[str] = []
    for pat, name in _BASE_LABELS:
        m = re.search(rf"(?:{pat})\s*(?:\([^)]*\))?\s*[:：]\s*(on|off)\b", t, re.IGNORECASE)
        if not m:
            return None                      # 셋 중 하나라도 못 읽으면 실패
        if m.group(1).lower() == "on":
            on.append(name)
    return ", ".join(on) if on else BASE_EMPTY


# kind → 변환 함수. 등록 안 된 kind 는 원문 그대로 저장된다.
PARSERS = {"BASE": parse_base}


def apply(kind: str, raw: str) -> tuple[str, bool]:
    """kind 별 저장값 변환 — (저장할 txt, 파싱 성공 여부). 실패 시 원문 유지."""
    parser = PARSERS.get(kind)
    if parser is None:
        return raw, True
    parsed = parser(raw)
    return (parsed, True) if parsed is not None else (raw, False)
