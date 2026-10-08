"""keel Product's presentation: one self-contained HTML file a product owner shows when they present an initiative.

Built from the initiative's documents (brief, impact, decision memo, and the plan once it exists). A 16:9 slide stage on
a big screen (← → to move, T to change the style), one card per slide on a phone. Every text from a document is
escaped (md.py), so a document cannot put markup or scripts into the page.
"""

from __future__ import annotations

import html
import re

from .md import inline, sections, to_html

STYLE_TOKENS = """
:root{--bg:#F6F4EE;--surface:#FFFFFF;--surface2:#EEEADF;--text:#1E2033;--muted:#585A76;--line:#DEDACC;--accent:#4B32C3;
--accent-soft:rgba(75,50,195,.09);--hot:#B35F06;--hot-soft:rgba(179,95,6,.11);--ok:#1D7F55;--bad:#B93C12;--stage:#D9D5C8;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-style]){--bg:#14162B;--surface:#1E2142;--surface2:#272B55;--text:#EEECF8;
--muted:#A9A7C7;--line:#323770;--accent:#8B70FF;--accent-soft:rgba(139,112,255,.16);--hot:#F2A93B;--hot-soft:rgba(242,169,59,.16);
--ok:#4CC38A;--bad:#FF8A80;--stage:#0B0C1A;color-scheme:dark}}
:root[data-style="keel"]{--bg:#14162B;--surface:#1E2142;--surface2:#272B55;--text:#EEECF8;--muted:#A9A7C7;--line:#323770;
--accent:#8B70FF;--accent-soft:rgba(139,112,255,.16);--hot:#F2A93B;--hot-soft:rgba(242,169,59,.16);--ok:#4CC38A;--bad:#FF8A80;
--stage:#0B0C1A;color-scheme:dark}
:root[data-style="paper"]{--bg:#F6F4EE;--surface:#FFFFFF;--surface2:#EEEADF;--text:#1E2033;--muted:#585A76;--line:#DEDACC;
--accent:#4B32C3;--accent-soft:rgba(75,50,195,.09);--hot:#B35F06;--hot-soft:rgba(179,95,6,.11);--ok:#1D7F55;--bad:#B93C12;
--stage:#D9D5C8;color-scheme:light}
:root[data-style="mono"]{--bg:#FFFFFF;--surface:#F5F5F4;--surface2:#EAEAE8;--text:#121212;--muted:#5C5C5C;--line:#E0E0DE;
--accent:#121212;--accent-soft:rgba(0,0,0,.06);--hot:#C2410C;--hot-soft:rgba(194,65,12,.09);--ok:#2B7A3D;--bad:#B42318;
--stage:#E6E6E4;color-scheme:light}
"""

STYLE = STYLE_TOKENS + """
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--stage);color:var(--text);font-family:'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;overflow:hidden}
.deck{position:absolute;width:1600px;height:900px;transform-origin:0 0;box-shadow:0 30px 80px rgba(0,0,0,.28)}
.slide{position:absolute;inset:0;display:none;flex-direction:column;gap:26px;padding:68px 88px 100px;background:var(--bg);overflow:hidden}
.slide.on{display:flex}
.body{flex:1;min-height:0;overflow:auto}
.foot{position:absolute;left:88px;right:88px;bottom:36px;display:flex;gap:16px;align-items:center;font:500 15px/1.2 'JetBrains Mono',ui-monospace,monospace;color:var(--muted)}
.foot .sp{flex:1}
.eyebrow{font:600 16px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
h1,h2,h3,h4{font-family:'Bricolage Grotesque','IBM Plex Sans',system-ui,sans-serif;font-weight:650;letter-spacing:-.015em;text-wrap:balance}
h1{font-size:72px;line-height:1.04} h2{font-size:48px;line-height:1.08} h3{font-size:26px;line-height:1.25} h4{font-size:21px}
p,li,td,th{font-size:20px;line-height:1.5}
.lead{font-size:26px;color:var(--muted);max-width:900px}
ul,ol{padding-left:26px;display:flex;flex-direction:column;gap:6px}
code{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.88em;background:var(--surface2);padding:1px 5px;border-radius:5px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:24px 26px;display:flex;flex-direction:column;gap:10px;min-width:0}
.card.acc{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.tag{display:inline-flex;padding:6px 12px;border-radius:999px;font:600 13px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase}
.tag.acc{background:var(--accent-soft);color:var(--accent)} .tag.hot{background:var(--hot-soft);color:var(--hot)}
.tag.ok{border:1.5px solid var(--ok);color:var(--ok)} .tag.bad{border:1.5px solid var(--bad);color:var(--bad)}
table{width:100%;border-collapse:collapse}
th{font:600 14px/1.3 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:0 12px 10px;border-bottom:1px solid var(--line)}
td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}
.title-grid{display:grid;grid-template-columns:1.3fr .9fr;gap:52px;align-items:center;flex:1}
.kv{display:grid;grid-template-columns:auto 1fr;gap:8px 16px}
.kv dt{font:600 14px/1.5 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.toolbar{position:fixed;left:50%;bottom:calc(10px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);display:flex;gap:6px;
align-items:center;padding:6px 8px;border-radius:12px;background:#1B1D36;color:#EEECF8;font:500 13px 'JetBrains Mono',monospace;z-index:9}
.toolbar button,.toolbar select{background:transparent;color:inherit;border:1px solid #3A3E6E;border-radius:8px;padding:6px 10px;font:inherit;cursor:pointer}
.toolbar select{color-scheme:dark}
body.reading{overflow:auto;height:auto}
.reading .deck{position:static;width:auto;height:auto;transform:none!important;box-shadow:none;display:flex;flex-direction:column;gap:16px;padding-inline:16px;padding-block:16px 88px}
.reading .slide{display:flex;position:relative;inset:auto;height:auto;padding:22px 18px;gap:16px;border-radius:16px;overflow:visible}
.reading .body{overflow:visible}
.reading .foot{position:static;flex-wrap:wrap;font-size:12px}
.reading h1{font-size:32px} .reading h2{font-size:25px} .reading h3{font-size:18px}
.reading p,.reading li,.reading td,.reading th,.reading .lead{font-size:15px}
.reading .grid,.reading .title-grid{grid-template-columns:1fr;gap:12px}
.reading .card{padding:16px}
.reading .table-wrap{overflow-x:auto}
.reading .nav{display:none}
"""

SCRIPT = """
(()=>{const root=document.documentElement,deck=document.getElementById('deck'),slides=[...document.querySelectorAll('.slide')],
count=document.getElementById('count'),sel=document.getElementById('style'),small=matchMedia('(max-width: 760px), (max-height: 560px)');let i=0;
slides.forEach((s,k)=>{const n=s.querySelector('.num');if(n)n.textContent=String(k+1).padStart(2,'0')});
function layout(){document.body.classList.toggle('reading',small.matches);if(small.matches){deck.style.transform='';return}
const room=innerHeight-64,s=Math.min(innerWidth/1600,room/900);deck.style.transform=`scale(${s})`;
deck.style.left=`${(innerWidth-1600*s)/2}px`;deck.style.top=`${Math.max(0,(room-900*s)/2)}px`}
function show(n){i=Math.max(0,Math.min(slides.length-1,n));slides.forEach((s,k)=>s.classList.toggle('on',k===i));count.textContent=`${i+1} / ${slides.length}`}
function style(v){if(v==='auto')delete root.dataset.style;else root.dataset.style=v;sel.value=v;try{localStorage.setItem('keel-product-style',v)}catch(e){}}
let saved=null;try{saved=localStorage.getItem('keel-product-style')}catch(e){}style(['auto','keel','paper','mono'].includes(saved)?saved:'auto');
layout();show(0);addEventListener('resize',layout);small.addEventListener?.('change',layout);
document.getElementById('prev').onclick=()=>show(i-1);document.getElementById('next').onclick=()=>show(i+1);sel.onchange=()=>style(sel.value);
addEventListener('keydown',e=>{if(e.target.closest('select'))return;if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();show(i+1)}
else if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();show(i-1)}else if(e.key.toLowerCase()==='t'){const o=['auto','keel','paper','mono'];style(o[(o.indexOf(sel.value)+1)%o.length])}})})();
"""

FONTS = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..700"
         "&family=IBM+Plex+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap")

MEMO_KEYS = ("value", "cost", "time", "risk")


def _esc(t) -> str:
    return html.escape(str(t if t is not None else ""), quote=True)


def _memo_facts(memo: str) -> list[tuple[str, str]]:
    """'Value: ...' style lines (or '## Value' sections' first line) from the decision memo, for the title card."""
    facts: dict[str, str] = {}
    for line in (memo or "").splitlines():
        m = re.match(r"^\s*[-*]?\s*\**(Value|Cost|Time|Risk)\**\s*[:·-]\s*(.+)$", line.strip(), re.I)
        if m and m.group(1).lower() not in facts:
            facts[m.group(1).lower()] = m.group(2).strip()
    for title, body in sections(memo):
        k = title.lower()
        if k in MEMO_KEYS and k not in facts and body:
            facts[k] = body.splitlines()[0].lstrip("-* ").strip()
    return [(k.capitalize(), facts[k]) for k in MEMO_KEYS if k in facts]


def _foot(ini: dict, note: str) -> str:
    return (f'<div class="foot"><span>{_esc(ini.get("id"))} · {_esc(ini.get("title"))}</span><span class="sp"></span>'
            f'<span>{_esc(note)}</span><span class="num"></span></div>')


def _cards(text: str, limit: int = 6) -> str:
    parts = [(t, b) for t, b in sections(text) if b][:limit]
    if not parts:
        return f'<div class="card">{to_html(text)}</div>'
    return '<div class="grid">' + "".join(
        f'<div class="card"><h3>{inline(t or "Summary")}</h3>{to_html(b, 4)}</div>' for t, b in parts) + "</div>"


def render(ini: dict, brief: str = "", impact_repos: list[dict] | None = None, impact: str = "", memo: str = "",
           recommend: str = "", plan: dict | None = None, versions: dict | None = None) -> str:
    """The presentation's HTML. impact_repos: [{repo, team, risk, text}]; plan: plan.check()'s result."""
    slides: list[str] = []
    facts = _memo_facts(memo)
    kv = "".join(f"<dt>{_esc(k)}</dt><dd>{inline(v)}</dd>" for k, v in facts)
    rec = f"Option {recommend}" if recommend else "See the options"
    slides.append(
        '<section class="slide" aria-label="Title"><div class="title-grid"><div style="display:flex;flex-direction:column;gap:22px">'
        f'<span class="eyebrow">Initiative {_esc(ini.get("id"))} · decision review</span><h1>{_esc(ini.get("title"))}</h1>'
        f'<p class="lead">{inline(ini.get("idea") or "")}</p>'
        f'<div class="row"><span class="tag acc">Owner · {_esc(ini.get("owner") or "Product")}</span>'
        f'<span class="tag ok">{_esc(ini.get("stage_label") or "Ready for go / no-go")}</span></div></div>'
        f'<div class="card acc"><span class="eyebrow">Recommendation</span><h2>{_esc(rec)}</h2>'
        f'{"<dl class=kv>" + kv + "</dl>" if kv else ""}</div></div>{_foot(ini, "Generated by keel Product")}</section>')
    if brief:
        slides.append(f'<section class="slide" aria-label="The brief"><span class="eyebrow">The brief</span><h2>Problem and goal</h2>'
                      f'<div class="body">{_cards(brief)}</div>{_foot(ini, "brief v" + str((versions or {}).get("brief", 1)))}</section>')
    if impact_repos:
        rows = "".join(
            f'<tr><td><b>{_esc(r.get("repo"))}</b><br><span style="color:var(--muted)">{_esc(r.get("team") or "")}</span></td>'
            f'<td><span class="tag {"bad" if str(r.get("risk", "")).lower() == "high" else "hot" if str(r.get("risk", "")).lower() == "medium" else "ok"}">'
            f'{_esc(r.get("risk") or "?")}</span></td><td>{inline(_first_lines(r.get("text") or "", 3))}</td></tr>' for r in impact_repos)
        unknowns = [ln.strip("-* ").strip() for r in impact_repos for ln in (r.get("text") or "").splitlines() if "UNKNOWN" in ln][:6]
        unk = ("<div class=card><h3>Unknowns</h3><ul>" + "".join(f"<li>{inline(u)}</li>" for u in unknowns) + "</ul></div>") if unknowns else ""
        slides.append(f'<section class="slide" aria-label="Impact"><span class="eyebrow">Impact</span><h2>What changes in our systems</h2>'
                      f'<div class="body" style="display:flex;flex-direction:column;gap:18px"><div class="table-wrap"><table><tr><th>Repo</th><th>Risk</th>'
                      f'<th>What changes</th></tr>{rows}</table></div>{unk}</div>{_foot(ini, "impact v" + str((versions or {}).get("impact", 1)))}</section>')
    elif impact:
        slides.append(f'<section class="slide" aria-label="Impact"><span class="eyebrow">Impact</span><h2>What changes in our systems</h2>'
                      f'<div class="body">{_cards(impact)}</div>{_foot(ini, "impact")}</section>')
    if memo:
        slides.append(f'<section class="slide" aria-label="Decision"><span class="eyebrow">Decision</span><h2>Value, cost, risk and options</h2>'
                      f'<div class="body">{_cards(memo, 8)}</div>{_foot(ini, "decision memo v" + str((versions or {}).get("decision", 1)))}</section>')
    if plan and plan.get("plan"):
        rows = ""
        for e in plan["plan"].get("epics") or []:
            for s in e.get("stories") or []:
                est = s.get("estimate_days") or []
                days = f"{est[0]}–{est[-1]}" if isinstance(est, list) and est else _esc(est)
                crit = " ★" if s.get("id") in (plan.get("critical_path") or []) else ""
                rows += (f'<tr><td><b>{_esc(s.get("id"))}</b>{crit}</td><td>{_esc(e.get("team"))}</td><td>{_esc(s.get("title"))}</td>'
                         f'<td>{days}</td><td>{_esc(", ".join(s.get("depends_on") or []) or "—")}</td></tr>')
        lo, hi = plan.get("total_days") or [0, 0]
        slides.append(f'<section class="slide" aria-label="Plan"><span class="eyebrow">Plan</span><h2>Teams, stories and the order</h2>'
                      f'<div class="body"><p class="lead">{plan["counts"]["stories"]} stories · {lo:g}–{hi:g} developer days · '
                      f'critical path ★ {plan.get("critical_days", 0):g} days</p><div class="table-wrap"><table><tr><th>Story</th><th>Team</th>'
                      f'<th>What</th><th>Days</th><th>After</th></tr>{rows}</table></div></div>{_foot(ini, "plan v" + str((versions or {}).get("plan", 1)))}</section>')
    vs = "".join(f"<li>{_esc(k)} v{_esc(v)}</li>" for k, v in (versions or {}).items())
    slides.append(f'<section class="slide" aria-label="Where this comes from"><span class="eyebrow">Appendix</span><h2>Where this comes from</h2>'
                  f'<div class="body"><div class="card"><p>Built by keel Product from initiative {_esc(ini.get("id"))}. When a document '
                  f'changes, keel builds this presentation again.</p>{"<ul>" + vs + "</ul>" if vs else ""}</div></div>'
                  f'{_foot(ini, "Generated by keel Product")}</section>')
    title = _esc(f"{ini.get('title') or 'Initiative'} · {ini.get('id') or ''}")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'<title>{title}</title><link rel="stylesheet" href="{FONTS}"><style>{STYLE}</style></head><body>'
            f'<main class="deck" id="deck">{"".join(slides)}</main>'
            '<nav class="toolbar" aria-label="Presentation controls"><button type="button" id="prev" class="nav" aria-label="Previous slide">‹</button>'
            '<span class="nav" id="count"></span><button type="button" id="next" class="nav" aria-label="Next slide">›</button>'
            '<select id="style" aria-label="Style"><option value="auto">Style: your theme</option><option value="keel">Style: keel</option>'
            '<option value="paper">Style: company light</option><option value="mono">Style: minimal</option></select></nav>'
            f'<script>{SCRIPT}</script></body></html>')


def _first_lines(text: str, n: int) -> str:
    out = [ln.strip("-* ").strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return " · ".join(out[:n])[:400]
