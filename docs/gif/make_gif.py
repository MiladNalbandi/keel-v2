"""Draws docs/keel-v2.gif: how keel v2 is set up and how a flow runs. Run: uv run --with pillow docs/gif/make_gif.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 960, 540
BG, PANEL, PANEL2, BORDER = "#131314", "#1a1a1c", "#222225", "#2c2c30"
FG, DIM, FAINT = "#f7f6f3", "#9a958c", "#6b675f"
ACCENT, OK, WARN, RUN, TERM = "#a48bff", "#4ec07c", "#e0a13a", "#57aeff", "#0d0d0e"

FONTS = "/System/Library/Fonts/"
def font(name, size, index=0):
    return ImageFont.truetype(FONTS + name, size, index=index)
SANS, SANS_B = font("HelveticaNeue.ttc", 20), font("HelveticaNeue.ttc", 20, 1)
TITLE, BIG = font("HelveticaNeue.ttc", 40, 1), font("HelveticaNeue.ttc", 26, 1)
SMALL, MONO, MONO_S = font("HelveticaNeue.ttc", 16), font("Menlo.ttc", 19), font("Menlo.ttc", 15)

frames: list[tuple[Image.Image, int]] = []


def txt(d, xy, text, font, fill):
    """d.text, but ◆ is drawn as a diamond (Helvetica Neue has no glyph for it)."""
    x, y = xy
    parts = text.split("◆")
    for i, part in enumerate(parts):
        if i:
            size = font.size * 0.62
            cy = y + font.size * 0.58
            d.polygon([(x + size / 2, cy - size / 2), (x + size, cy), (x + size / 2, cy + size / 2), (x, cy)], fill=fill)
            x += size + 4
        if part:
            d.text((x, y), part, font=font, fill=fill)
            x += d.textlength(part, font=font)
    return x


def tlen(d, text, font):
    return sum(d.textlength(p, font=font) for p in text.split("◆")) + text.count("◆") * (font.size * 0.62 + 4)


def canvas():
    im = Image.new("RGB", (W, H), BG)
    return im, ImageDraw.Draw(im)


def step_bar(d, n):
    labels = ["1 install", "2 start", "3 log in", "4 describe", "5 watch + approve", "6 done"]
    x = 40
    for i, l in enumerate(labels, 1):
        w = d.textlength(l, font=SMALL) + 24
        fill = ACCENT if i == n else PANEL2
        d.rounded_rectangle((x, 18, x + w, 46), 14, fill=fill)
        d.text((x + 12, 22), l, font=SMALL, fill=BG if i == n else DIM)
        x += w + 8


def caption(d, text, sub=""):
    txt(d, (40, 470), text, BIG, FG)
    if sub:
        d.text((40, 505), sub, font=SMALL, fill=DIM)


def terminal(d, lines, top=70, height=370):
    d.rounded_rectangle((40, top, W - 40, top + height), 12, fill=TERM, outline=BORDER)
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((58 + i * 20, top + 14, 70 + i * 20, top + 26), fill=c)
    y = top + 46
    for text, color in lines:
        d.text((64, y), text, font=MONO, fill=color)
        y += 30


def browser(d, url, top=70, height=380):
    d.rounded_rectangle((40, top, W - 40, top + height), 12, fill=PANEL, outline=BORDER)
    d.rounded_rectangle((140, top + 10, W - 140, top + 34), 12, fill=PANEL2)
    d.text((158, top + 13), url, font=MONO_S, fill=DIM)
    # keel mascot + sidebar
    d.rounded_rectangle((52, top + 48, 200, top + height - 12), 10, fill=BG)
    d.text((70, top + 60), "keel", font=SANS_B, fill=ACCENT)
    for i, item in enumerate(["Flow", "Live agents", "Jobs", "Repo", "Workflows", "Budget", "Connections"]):
        d.text((70, top + 100 + i * 32), item, font=SMALL, fill=FG if i == 0 else DIM)
    return 220, top + 52


def add(im, ms):
    frames.append((im, ms))


# 0 title
im, d = canvas()
d.text((40, 150), "keel v2", font=font("HelveticaNeue.ttc", 64, 1), fill=ACCENT)
d.text((40, 235), "AI agents write tested code, step by step,", font=BIG, fill=FG)
d.text((40, 272), "and you approve every important step.", font=BIG, fill=FG)
d.text((40, 340), "One Docker container · Claude, Codex/GPT or GitHub Copilot · your project folder", font=SANS, fill=DIM)
add(im, 2600)

# 1 install
for k in range(3):
    im, d = canvas(); step_bar(d, 1)
    lines = [("$ curl -fsSL …/keel-v2/main/install.sh | bash", FG)]
    out = [("  ✓ keel2 installed: ~/.local/bin/keel2", OK), ("  ✓ Docker is running", OK), ("  ✓ image ready", OK)]
    terminal(d, lines + out[:k + 1])
    caption(d, "Install once.", "Needs Docker. Puts one command, keel2, on your PATH.")
    add(im, 900 if k < 2 else 2200)

# 2 start
for k in range(2):
    im, d = canvas(); step_bar(d, 2)
    lines = [("$ keel2 start ~/my-app", FG), ("  Starting keel for /Users/you/my-app…", DIM)]
    if k:
        lines += [("  ✓ keel is running: http://localhost:8080", OK),
                  ("    Next: Control › Connections › Set up login", DIM)]
    terminal(d, lines)
    caption(d, "Start it for your project.", "keel2 checks Docker, the port and git first. Add --docker if tests need Docker.")
    add(im, 1000 if k == 0 else 2400)

# 3 log in
im, d = canvas(); step_bar(d, 3)
x, y = browser(d, "localhost:8080 / Control › Connections")
d.text((x, y), "Connections", font=BIG, fill=FG)
rows = [("Claude (subscription)", "logged in", OK), ("Codex / GPT", "Set up login", ACCENT), ("GitHub Copilot", "Set up login", ACCENT)]
for i, (name, state, color) in enumerate(rows):
    yy = y + 50 + i * 62
    d.rounded_rectangle((x, yy, W - 60, yy + 50), 10, fill=PANEL2)
    d.text((x + 16, yy + 14), name, font=SANS, fill=FG)
    d.text((W - 220, yy + 14), ("✓ " if color == OK else "") + state, font=SANS_B, fill=color)
d.text((x, y + 250), "Log in from the page; the login is saved encrypted in keel's own database.", font=SMALL, fill=DIM)
caption(d, "Log in once.", "Your subscription or an API key. Nothing is copied from your computer.")
add(im, 2600)

# 4 describe
im, d = canvas(); step_bar(d, 4)
x, y = browser(d, "localhost:8080 / Run › Flow › Start a flow")
d.text((x, y), "Start a flow", font=BIG, fill=FG)
d.text((x, y + 48), "Workflow", font=SMALL, fill=DIM)
d.rounded_rectangle((x, y + 70, x + 300, y + 104), 8, fill=PANEL2); d.text((x + 12, y + 77), "feature (keel)", font=SANS, fill=FG)
d.text((x, y + 120), "Describe it for the agents", font=SMALL, fill=DIM)
d.rounded_rectangle((x, y + 142, W - 60, y + 222), 8, fill=PANEL2)
d.text((x + 12, y + 152), "Show each player's rank next to their best score;", font=SANS, fill=FG)
d.text((x + 12, y + 180), "ties share a rank.", font=SANS, fill=FG)
d.rounded_rectangle((x, y + 240, x + 120, y + 276), 8, fill=ACCENT); d.text((x + 28, y + 247), "Start", font=SANS_B, fill=BG)
caption(d, "Say what you want, in your words.", "keel makes its own branch and never touches your uncommitted files.")
add(im, 2600)

# 5 the flow: graph with progress
NODES = ["spec", "◆ spec", "red", "verify", "green", "◆ AC gate", "review", "◆ final", "done"]
WAITS = {1, 5, 7}
COMMITS = {3: "test(AC-1): rank of a player's best score", 4: "feat(AC-1): rank of a player's best score",
           6: "fix(review): address review findings"}
NOTE = {0: "explorer reads the code and writes the spec", 1: "◆ waits for you: approve the criteria",
        2: "test-author writes a failing test", 3: "keel runs it: it must fail for the right reason",
        4: "implementer writes the smallest code that passes", 5: "◆ waits for you: approve this criterion",
        6: "4 reviewers check the whole branch", 7: "◆ waits for you: final review", 8: "all criteria done"}


def flow_frame(cur, show_gate=False):
    im, d = canvas(); step_bar(d, 5 if cur < 8 else 6)
    x0, y0 = browser(d, "localhost:8080 / Run › Flow")
    d.text((x0, y0), "player-rank · feature", font=SANS_B, fill=FG)
    d.text((x0 + 260, y0 + 2), f"branch feat/player-rank · tokens {12 + cur * 9}k", font=SMALL, fill=DIM)
    nx, ny, nw, gap = x0, y0 + 50, 66, 8
    for i, n in enumerate(NODES):
        x = nx + i * (nw + gap)
        if i < cur or i == cur == len(NODES) - 1:
            fill, fg, outline = "#173225", OK, OK
        elif i == cur:
            fill, fg, outline = ("#36290f", WARN, WARN) if i in WAITS else ("#142a3d", RUN, RUN)
        else:
            fill, fg, outline = PANEL2, FAINT, BORDER
        d.rounded_rectangle((x, ny, x + nw, ny + 40), 8, fill=fill, outline=outline, width=2)
        t = n.replace("◆ ", "◆")
        f = SMALL if tlen(d, t, SMALL) < nw - 8 else font("HelveticaNeue.ttc", 12)
        txt(d, (x + (nw - tlen(d, t, f)) / 2, ny + 11 if f is SMALL else ny + 13), t, f, fg)
        if i < len(NODES) - 1:
            d.line((x + nw, ny + 20, x + nw + gap, ny + 20), fill=OK if i < cur else BORDER, width=2)
    txt(d, (x0, ny + 58), NOTE[cur], SANS, WARN if cur in WAITS else FG)
    d.text((x0, ny + 100), "commits", font=SMALL, fill=DIM)
    yy = ny + 124
    for k in sorted(COMMITS):
        if k <= cur:
            d.text((x0, yy), "●", font=SMALL, fill=OK)
            d.text((x0 + 18, yy - 1), COMMITS[k], font=MONO_S, fill=FG)
            yy += 26
    if show_gate:
        gx, gy = x0, 360
        d.rounded_rectangle((gx, gy, W - 60, gy + 66), 10, fill="#36290f", outline=WARN)
        txt(d, (gx + 14, gy + 8), "◆ waits for you", SMALL, WARN)
        d.rounded_rectangle((gx + 14, gy + 30, gx + 114, gy + 58), 7, fill=OK); d.text((gx + 28, gy + 34), "Approve", font=SMALL, fill=BG)
        d.rounded_rectangle((gx + 124, gy + 30, gx + 284, gy + 58), 7, fill=PANEL2); d.text((gx + 138, gy + 34), "Send back + why", font=SMALL, fill=FG)
        d.text((gx + 300, gy + 36), "a sound and a pop-up tell you", font=SMALL, fill=DIM)
    if cur < 8:
        caption(d, "Watch the agents; approve at the ◆ gates.", "Every step, file change and command is visible in Live agents and Jobs.")
    else:
        caption(d, "Done: tested commits on their own branch.", "You review and push. keel never pushes for you.")
    return im


for cur in range(len(NODES)):
    if cur in WAITS:
        add(flow_frame(cur), 900)
        add(flow_frame(cur, show_gate=True), 2200)
    else:
        add(flow_frame(cur), 1300 if cur < 8 else 3200)

# end card
im, d = canvas()
d.text((40, 110), "That's it.", font=TITLE, fill=FG)
terminal(d, [("$ curl -fsSL …/install.sh | bash", FG), ("$ keel2 start ~/my-app", FG),
             ("  → log in, describe the work, approve the gates", DIM)], top=180, height=150)
d.text((40, 360), "keel2 doctor   checks your setup and says how to fix problems", font=MONO_S, fill=DIM)
d.text((40, 386), "keel2 status   where it runs · keel2 stop · keel2 update", font=MONO_S, fill=DIM)
d.text((40, 450), "github.com/MiladNalbandi/keel-v2", font=BIG, fill=ACCENT)
add(im, 3500)

out = Path(__file__).resolve().parents[1] / "keel-v2.gif"
imgs = [f.convert("P", palette=Image.ADAPTIVE, colors=64) for f, _ in frames]
imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=[ms for _, ms in frames], loop=0, optimize=True)
print(out, len(frames), "frames", round(out.stat().st_size / 1024), "KB")
