"""판독값 변화 검출 — 순수 함수 (agent-vision3 read/change 이식).

판독값(txt)을 kind 별로 idx 순서대로 훑으며 직전 값과 달라진 프레임을 찾는다. 마킹은
프레임 단위 — 한 항목이라도 바뀌면 그 프레임을 변화로 본다.

비교 규칙:
- ETC(타자·투수 기록)는 제외한다(persistence.details.CHANGE_EXCLUDE_KINDS) — 타석마다
  바뀌어 넣으면 거의 모든 프레임이 변화가 된다.
- 값이 빈 프레임은 비교에서 빠진다(fetch_txt_series 가 걸러 준다). 간격 샘플링으로
  판독하지 않은 프레임을 넣으면 '값 → 빈값 → 값' 이 가짜 변화 2건이 된다.
- 각 kind 의 첫 등장 값도 변화로 본다(없다가 생긴 것도 변화).
"""


def _norm_team(txt: str) -> str:
    """'KIA 8: 삼성 3' 과 '삼성 3: KIA 8' 을 같은 값으로 본다.

    판독이 팀 순서를 프레임마다 뒤집는 특성이 있다(값은 맞는데 순서만 다름). 정규화하지
    않으면 점수가 그대로인데도 순서가 뒤집힐 때마다 가짜 변화가 잡힌다.
    """
    parts = [p.strip() for p in txt.split(":")]
    return " | ".join(sorted(parts)) if len(parts) == 2 else txt


# kind 별 비교용 정규화. 등록 안 된 kind 는 원문 그대로 비교한다.
# 판독 형식이 바뀌어 가짜 변화가 생기면 여기에 함수를 추가한다.
NORMALIZERS = {"TEAM": _norm_team}


def detect(series: list[tuple[str, int, str]]) -> tuple[set[int], dict[str, int]]:
    """
    Summary:
        (kind, idx, txt) 목록에서 값이 바뀐 프레임 idx 집합을 뽑는다.
    Args:
        series: fetch_txt_series 결과. kind 별 idx 오름차순이어야 한다.
    Returns:
        tuple[set[int], dict[str, int]]: (변화 프레임 idx, kind 별 변화 건수).
    Description:
        - 순수 함수 — DB 를 모른다. 정렬 가정이 깨져도 안전하도록 내부에서 다시 정렬한다.
    """
    changed: set[int] = set()
    per_kind: dict[str, int] = {}
    by_kind: dict[str, list[tuple[int, str]]] = {}
    for kind, idx, txt in series:
        by_kind.setdefault(kind, []).append((idx, txt))

    for kind, rows in sorted(by_kind.items()):
        norm = NORMALIZERS.get(kind, lambda t: t)
        prev = None
        n = 0
        for idx, txt in sorted(rows):
            key = norm(txt)
            if key != prev:          # 첫 등장(prev=None)도 변화로 본다
                changed.add(idx)
                prev = key
                n += 1
        per_kind[kind] = n
    return changed, per_kind
