# Draws keel v2's AC loop and the whole feature flow as SVG (then rsvg-convert makes PNGs).
from html import escape

FONT = "Helvetica, Arial, sans-serif"
C = {  # fill, stroke, label colour
    "agent": ("#efe9ff", "#7a5cff", "#4b2fd1"),
    "code": ("#e6f1fb", "#0b74c4", "#0b5a99"),
    "gate": ("#fff2db", "#c27c00", "#8a5600"),
    "start": ("#f1efea", "#9b968c", "#3d3a35"),
    "branch": ("#f4f4f4", "#9b968c", "#3d3a35"),
}
KIND = {"agent": "AGENT", "code": "KEEL", "gate": "YOU · GATE", "start": "", "branch": "CHECK"}

class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.out = w, h, []
    def add(self, s): self.out.append(s)
    def text(self, x, y, s, size=14, weight="400", fill="#131314", anchor="start", italic=False):
        st = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{st}>{escape(s)}</text>')
    def box(self, x, y, w, h, kind, title, sub=None, who=None):
        fill, stroke, lab = C[kind]
        if kind == "gate":
            self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="22" fill="{fill}" stroke="{stroke}" stroke-width="2.4"/>')
            self.add(f'<path d="M{x+20},{y+h/2} l10,-10 l10,10 l-10,10 z" fill="{stroke}"/>')
            tx = x + 50
        else:
            self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
            tx = x + 16
        tag = KIND[kind] + (f" · {who}" if who else "")
        if tag:
            self.text(tx, y + 22, tag, 11.5, "700", lab)
        self.text(tx, y + (44 if tag else 28), title, 16, "700", "#131314")
        for i, line in enumerate((sub or "").split("\n") if sub else []):
            self.text(tx, y + 66 + i * 19, line, 13.5, "400", "#4a463f")
    def arrow(self, x1, y1, x2, y2, color="#6f6b63", width=2, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" marker-end="url(#ah-{color[1:]})"{d}/>')
    def path(self, d, color="#6f6b63", width=2, dash=None):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" marker-end="url(#ah-{color[1:]})"{dd}/>')
    def render(self, colors):
        marks = "".join(f'<marker id="ah-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>' for c in colors)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                f'<defs>{marks}</defs><rect width="100%" height="100%" fill="#faf9f5"/>' + "".join(self.out) + "</svg>")

COLORS = ["#6f6b63", "#c0392b", "#1f8a4c", "#c27c00", "#7a5cff"]

def legend(s, y):
    x = 40
    for kind, label in (("agent", "an agent works (its own fresh context)"), ("code", "keel itself: runs tests, commits"), ("gate", "a gate: you decide")):
        fill, stroke, _ = C[kind]
        s.add(f'<rect x="{x}" y="{y}" width="22" height="16" rx="{8 if kind=="gate" else 4}" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
        s.text(x + 30, y + 13, label, 13, "400", "#3d3a35")
        x += 300
    s.add(f'<line x1="40" y1="{y+36}" x2="80" y2="{y+36}" stroke="#c0392b" stroke-width="2.4"/>')
    s.text(88, y + 41, "a way back (send back, a failing check)", 13, "400", "#3d3a35")
    s.add(f'<line x1="400" y1="{y+36}" x2="440" y2="{y+36}" stroke="#1f8a4c" stroke-width="2.4"/>')
    s.text(448, y + 41, "approve / pass: go on", 13, "400", "#3d3a35")

# ---------------------------------------------------------------- picture 1: the AC loop
def ac_loop():
    s = Svg(1150, 1420)
    s.text(40, 52, "keel v2 · the acceptance-criteria loop", 28, "700")
    s.text(40, 82, "One round for each criterion (AC-1, AC-2, …), in order. Test first, then code, then your gate.", 15, "400", "#4a463f")
    X, W = 330, 470
    cx = X + W / 2
    y = 120
    s.box(X, y, W, 52, "start", "Criterion AC-n starts"); y_start = y
    s.arrow(cx, y + 52, cx, y + 80)
    y = 200; s.box(X, y, W, 96, "agent", "RED · write a failing test", "for this criterion only, no production code;\nit never sees the implementation plan", "test-author"); y_red = y
    s.arrow(cx, y + 96, cx, y + 124)
    y = 320; s.box(X, y, W, 96, "code", "verify_red", "keel runs the test: it must FAIL for the right\nreason · then keel commits test(AC-n)"); y_vr = y
    s.arrow(cx, y + 96, cx, y + 124, "#1f8a4c")
    s.text(cx + 8, y + 116, "fails as it should", 12.5, "400", "#1f8a4c")
    y = 440; s.box(X, y, W, 96, "agent", "GREEN · the smallest code that passes", "only what this test needs; tests are frozen\nin this phase (the guard refuses edits)", "implementer"); y_green = y
    s.arrow(cx, y + 96, cx, y + 124)
    y = 560; s.box(X, y, W, 96, "code", "verify_green", "keel runs the tests: they must PASS\n· then keel commits feat(AC-n)"); y_vg = y
    s.arrow(cx, y + 96, cx, y + 124, "#1f8a4c")
    s.text(cx + 8, y + 116, "green", 12.5, "400", "#1f8a4c")
    y = 680; s.box(X, y, W, 96, "agent", "AC review", "a fresh reviewer reads RED + GREEN: is the\ncriterion met, does the test prove it?", "ac-reviewer"); y_rev = y
    s.arrow(cx, y + 96, cx, y + 124)
    y = 800; s.box(X, y, W, 96, "gate", "AC gate · you decide", "commits, test results and the review are on the\ncard (Flow page, Inbox, phone notification)"); y_gate = y
    # approve
    s.arrow(cx, y + 96, cx, y + 150, "#1f8a4c", 2.4)
    s.text(cx + 10, y + 128, "approve", 14, "700", "#1f8a4c")
    y = 950; s.box(X, y, W, 74, "start", "Next criterion: AC-n+1 · round again", "after the last one: integration, then ship")
    # loop: next criterion back to start (left side)
    s.path(f"M{X},{y+37} C{X-36},{y+37} {X-36},{y_start+26} {X},{y_start+26}", "#1f8a4c", 2.2)
    s.text(X - 34, y - 40, "next AC", 13, "700", "#1f8a4c", "end")
    # send back: from gate right side to RED right side
    R = X + W
    s.path(f"M{R},{y_gate+48} C{R+250},{y_gate+48} {R+250},{y_red+48} {R},{y_red+48}", "#c0392b", 2.6)
    my = (y_gate + y_red) / 2 + 48
    s.text(R + 200, my - 10, "send back", 14, "700", "#c0392b")
    s.text(R + 200, my + 8, "with your note:", 12.5, "400", "#c0392b")
    s.text(R + 200, my + 25, "a new RED round", 12.5, "400", "#c0392b")
    # verify_green fails -> back to GREEN (inner right loop)
    s.path(f"M{R},{y_vg+40} C{R+56},{y_vg+40} {R+56},{y_green+56} {R},{y_green+56}", "#c0392b", 2.2)
    for i, line in enumerate(("still red:", "GREEN again", "(3 tries, then", "keel asks you)")):
        s.text(R + 56, y_vg - 6 + i * 17, line, 12.5, "700" if i < 2 else "400", "#c0392b")
    # verify_red passes at once -> already met gate (left)
    s.box(24, 300, 250, 176, "gate", "Already met?", "the test passed before\nany code was written\napprove: AC done, test kept\nsend back: a stricter test")
    s.arrow(X, y_vr + 48, 276, y_vr + 48, "#c27c00", 2)
    # notes
    ny = 1060
    s.add(f'<rect x="40" y="{ny}" width="920" height="210" rx="12" fill="#ffffff" stroke="#e3e0d9"/>')
    s.text(60, ny + 32, "Good to know", 16, "700")
    notes = [
        "• The spec needs a change? RED or GREEN can stop for a spec amendment: you approve it at its own gate, then RED again.",
        "• Each agent starts fresh: the test author never sees the plan; the reviewer sees only the diff and the criterion.",
        "• keel's guard checks every file an agent writes: in RED only tests may change, in GREEN tests stay frozen.",
        "• Gate mode \"end\" skips this gate (one review at the end). Run mode \"important\" or \"auto\" can approve a clean",
        "  gate by itself (checks green, review clean); it is logged and listed in the final review and the PR.",
        "• At a waiting gate the Helper can fix things for you (Fix mode); Done runs the checks and keel commits.",
    ]
    for i, n in enumerate(notes):
        s.text(60, ny + 62 + i * 24, n, 13.5, "400", "#3d3a35")
    legend(s, 1310)
    return s.render(COLORS)

# ---------------------------------------------------------------- picture 2: the whole feature flow
def feature():
    s = Svg(1000, 4000)
    s.text(40, 52, "keel v2 · the whole feature flow", 28, "700")
    s.text(40, 82, "From your request to a pull request. Every ◆ is a gate where you decide; keel never pushes.", 15, "400", "#4a463f")
    X, W = 260, 480
    cx = X + W / 2
    y = 110
    rows = []
    def phase(label, color="#7a5cff"):
        nonlocal y
        s.add(f'<rect x="40" y="{y}" width="920" height="30" rx="6" fill="{color}" opacity="0.10"/>')
        s.text(56, y + 21, label, 14, "700", color)
        y += 44
    def step(kind, title, sub=None, who=None, h=None, gap=26):
        nonlocal y
        hh = h or (74 if sub and "\n" not in sub else 96 if sub else 56)
        s.box(X, y, W, hh, kind, title, sub, who)
        top = y
        y += hh
        s.arrow(cx, y, cx, y + gap - 2)
        y += gap
        return top, hh
    def back(frm, to, label, side="right", color="#c0392b", off=70):
        (fy, fh), (ty, th) = frm, to
        if side == "right":
            R = X + W
            s.path(f"M{R},{fy+fh/2} C{R+off},{fy+fh/2} {R+off},{ty+th/2} {R},{ty+th/2}", color, 2.2)
            s.text(R + off - 8, (fy + ty) / 2 + th / 2 + 4, label, 12.5, "700", color)
        else:
            s.path(f"M{X},{fy+fh/2} C{X-off},{fy+fh/2} {X-off},{ty+th/2} {X},{ty+th/2}", color, 2.2)
            s.text(X - off - 4, (fy + ty) / 2 + th / 2 + 4, label, 12.5, "700", color, "end")

    s.box(X, y, W, 56, "start", "Your request: a task, a Jira ticket, an idea"); y += 56; s.arrow(cx, y, cx, y + 24); y += 26
    phase("1 · PREFLIGHT")
    step("code", "preflight + own branch", "a git repo, a test command, branch feat/<slug>")
    phase("2 · SPEC")
    sp = step("agent", "spec: the interview", "asks what is unclear, writes the criteria", "explorer")
    step("agent", "explore: one explorer per area", "api, web, data, in parallel: what the change touches", "explorers")
    step("agent", "plan under the criteria", "how each criterion will be built", "explorer")
    sg = step("gate", "spec gate", "approve · send back · edit · ask for a review")
    back(sg, sp, "send back", "right")
    step("code", "freeze + commit the spec", "docs/specs/<slug>.md")
    step("gate", "which optional phases run", "contract, integration, e2e, smoke")
    phase("3 · CONTRACT (optional)")
    step("agent", "contract", "the API contract (OpenAPI) delta", "contract-author")
    step("gate", "contract gate", "the delta, then keel commits it")
    phase("4 · THE ACCEPTANCE-CRITERIA LOOP (picture 1)", "#c27c00")
    lp = step("agent", "for each AC: RED → verify_red → GREEN → verify_green", "test-author, implementer, keel's checks and commits", "per criterion")
    ag = step("gate", "AC gate (per criterion)", "approve: next AC · send back: RED again")
    back(ag, lp, "next AC / send back", "right", "#c27c00", 90)
    phase("5 · INTEGRATION + SECURITY")
    ig = step("agent", "integration", "the parts work together; keel runs the suite, commits", "implementer")
    gi = step("gate", "integration gate")
    back(gi, ig, "send back", "right")
    step("agent", "security", "dependency check, security auditor + dependency triager", "in parallel")
    phase("6 · FULL-DIFF REVIEW")
    rf = step("agent", "review fix", "fixes what the full-diff review found", "implementer")
    cr = step("agent", "full-diff review", "the whole branch in a fresh context", "code-reviewer")
    back(cr, rf, "findings", "left")
    phase("7 · E2E + SMOKE (when the flow has them)")
    e2 = step("agent", "e2e specs + run", "Playwright for the [WEB] criteria", "e2e-author")
    ge = step("gate", "e2e gate · ◆ smoke gate")
    back(ge, e2, "send back", "right")
    phase("8 · SHIP")
    step("gate", "ship plan", "which ship steps run (a skip needs a reason)")
    vf = step("code", "verify · lint · release · coverage · deps · audit · trace", "a failing check goes to a fix agent first")
    step("agent", "review lenses + spec walk", "reviewers in parallel: behaviour no criterion asks for", "reviewers")
    fr = step("gate", "final review", "every exception and the verdicts for HEAD")
    back(fr, vf, "send back", "right", off=110)
    step("agent", "knowledge update + check", "the project's knowledge pages stay true", "librarian")
    step("code", "PR body", "spec, criteria trace, coverage, skips, Helper changes")
    pg = step("gate", "open the PR", "keel opens it with your token; it never pushes")
    s.box(X, y, W, 56, "start", "close: ADR, done · tested commits on the branch"); y += 56
    legend(s, y + 30)
    s.h = int(y + 110)          # the canvas fits what was drawn
    return s.render(COLORS)

import sys
out = sys.argv[1]
open(f"{out}/keel-ac-loop.svg", "w").write(ac_loop())
open(f"{out}/keel-feature-flow.svg", "w").write(feature())
