"""kind별 판독 프롬프트 — agent-vision3 read(← poc-vision-flow) 검증 완료본 이식.

**프롬프트가 곧 DB 규약이다**: 응답이 (BASE 제외) 후처리 없이 t_frame_board_detail.txt 에
원문 그대로 저장된다. txt 는 varchar(128) — 응답이 그 안에 들어와야 한다.
문구는 원본 실측(A/B) 근거가 걸려 있으므로 임의 수정 금지 — 수정 시 poc-vision-flow 의
eval/truth.tsv 로 회귀 채점할 것. 판정 규칙에 색상명 하드코딩 금지(방송사마다 스타일 상이).

원본 대비 요지(상세 근거는 agent-vision3 sports/baseball/read/prompts.py 주석):
- TEAM: 홈/원정이 아니라 **화면 위치**(위→아래/왼→오른) 순서로 못박음 — 좌우 뒤집힘 11건 → 0건.
- COUNT: 전환 애니메이션에 걸린 숫자 조각은 '?' 로 보류 — 지어낸 값 14건 제거.
- OUT: 밝기 배제, 채도(유채색)만 셈 — 어두운 배경 오답 83건 전멸(85.1% → 100%).
- BASE: tie-break 없이 각 베이스를 패널 배경과 대비 — 만루 오독 25건 해소(96.4% → 100%).
"""

P_TEAM = """\
This is the team name and score area of a baseball scoreboard graphic. Read both team names and both scores, then pair each score with the team it belongs to.

Layout Rules:
- The two teams may be stacked vertically (one per row), or placed side by side horizontally.
- In a horizontal layout the two names may sit at the far left and far right edges with both scores together in the middle. Each score belongs to the team on its own side: the left score belongs to the left name, the right score belongs to the right name.
- Decide which score belongs to which team before writing the answer. Never carry a score over to the other team.

Output Order (fixed - never vary it):
- Stacked vertically: the top team first, the bottom team second.
- Side by side horizontally: the leftmost team first, the rightmost team second.
- Do not reorder the teams for any other reason.

Do not output any text other than the specified format.

[Output Format]
<First_Team> <First_Score>: <Second_Team> <Second_Score>

[Example]
KIA 8: 삼성 2
"""

P_INNING = """\
This is the inning indicator on a baseball scoreboard graphic. Read the number and the direction arrow symbol.
- Up arrow (▲) = 초 (Top)
- Down arrow (▼) = 말 (Bottom)

Arrow State Criteria:
- An arrow is ACTIVE only if its fill color strongly contrasts with the background.
- Ignore INACTIVE arrows (hollow outlines, dimmed, or sharing a similar color with the background).

Do not output any text other than the specified format.

[Output Format]
<Inning_Number>회<초|말>

[Example]
5회말
"""

P_COUNT = """\
This is the Ball-Strike (B-S) count area. Read the two numbers or active indicator lights in order: Ball (B) then Strike (S).

Extraction Rules:
- If displayed as numbers: Read the two digits directly (Ball count - Strike count).
- If displayed as lights/symbols: Count the active (lit) lights for the first group (Ball), then the second group (Strike).

Active Light Criteria:
1. Compare interior colors: If only some circles are colored differently, the more vivid/bright ones are ACTIVE (ON), and the rest are INACTIVE (OFF).
2. If all circles look identical: Achromatic (grayish) or dim circles mean ALL OFF (0). Vivid, saturated colors mean ALL ON.

Digit Caught Mid-Change:
- The board animates a digit while it changes. A frame caught during that animation shows the digit broken into disconnected pieces - a floating arc, a stray bar, a half shape with a gap through it - instead of one solid connected glyph.
- Such a fragment is NOT a digit. Do not guess which digit it is turning into, and do not pick the digit it merely resembles.
- Judge each position on its own: a position is a digit only when its shape is complete and solid, like a normally rendered digit. If it is broken or partial, output ? for that position.

Output the two positions separated by a hyphen (-). Do not output any other text.

[Output Format]
<Ball_Count>-<Strike_Count>

[Example]
3-2

[Example when the ball position is mid-change]
?-2
"""

P_OUT = """\
This is the Out (O) count area of a baseball scoreboard. If displayed as a number, read the number directly. If displayed as circles/dots, count how many are lit.

Judge in this order:
1. Count how many circles there are in total (usually 2 or 3).
2. Count how many of them are filled with a clearly saturated (chromatic) color. That count is the answer.
3. Circles that look achromatic (grayscale) are NOT counted - regardless of whether they appear brighter or darker than the background.
4. If no circle is filled with a saturated color, the answer is 0.

Examples:
- 2 achromatic circles (even if they look bright against a dark background) -> 0
- 1 saturated circle + 1 achromatic circle -> 1
- 2 saturated circles -> 2

Output ONLY a single digit. Do not output brackets, hyphens, or extra text.

[Output Format]
0 | 1 | 2 | 3

[Example]
1
"""

P_BASE = """\
You are an expert Sports OCR and Scoreboard Analysis AI specializing in baseball broadcast graphics (Scorebugs) across all major leagues and international tournaments (MLB, KBO, WBC, Olympics, WBSC, NPB).
Your task is to accurately extract live base runner status from any broadcast image regardless of language or network style.

Analyze the attached baseball broadcast scorebug image and extract the current base runner status according to the guidelines below.

[Extraction Guidelines]
1. Base Position & Directional Mapping:
   - Top : 2nd Base (2루)
   - Right : 1st Base (1루)
   - Left : 3rd Base (3루)

2. Base Occupation & Complementary Color Rules:
   - Determine base occupation (`On`/`Off`) strictly by comparing the interior fill color of each base against the background/frame color of the scorebug.
   - A base is **OCCUPIED (`On`) ONLY if** its interior fill uses a color that is **complementary to or strongly contrasts with the background color** (e.g., bright Yellow/Orange/Red on Dark Blue/Black background, or vice versa).
   - A base is **UNOCCUPIED (`Off`)** if its interior fill **matches, blends with, or shares a similar tone with the background color** - a muted gray only slightly lighter or darker than the panel around it, or a hollow outline.
   - **Darkness is not emptiness.** A near-black solid fill on a light panel is strongly contrasting, so it is `On`, not `Off`.
   - **How big must the difference be?** An empty base is only *subtly* different from the panel - a muted gray you have to look closely to notice. An occupied base is *unmistakable at a glance*: either a saturated color, or at the opposite extreme of brightness from the panel (near-white on a dark panel, near-black on a light panel).
   - A muted mid-gray is `Off` even when the panel behind it is much darker. Being merely lighter than a dark panel is NOT enough - it must be near-white or vividly colored.
   - **All three identical:** This is normal - bases-loaded and bases-empty both look uniform. Do NOT switch to a different rule; keep judging each base against the panel background.

[Output Rules]
- Do NOT include any analysis, reasoning, or explanation.
- Output ONLY the 4 lines of the block below, nothing before or after it.

[Output Example Format]
First (Right) : <On|Off>
Second (Top) : <On|Off>
Third (Left) : <On|Off>

[Output Example]
First (Right) : On
Second (Top) : On
Third (Left) : Off
"""

P_ETC = """\
This is the information/player text area of a baseball scoreboard graphic. Transcribe all visible text exactly as shown.

Transcription Rules:
1. Position labels inside boxes or icon borders must be extracted as plain text without brackets or symbols (e.g., [LF] -> LF).
2. Separate line breaks with ' / ' (space-slash-space).
3. Do not output any explanation, commentary, or extra words other than the transcribed text.

[Output Format]
<Line_1> / <Line_2> / ...

[Example]
네일 P 9 / 3 LF 구자욱 .348
"""

PROMPTS = {
    "TEAM": P_TEAM,
    "INNING": P_INNING,
    "COUNT": P_COUNT,
    "OUT": P_OUT,
    "BASE": P_BASE,
    "ETC": P_ETC,
}
