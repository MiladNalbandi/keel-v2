// The Record page's own look (keel 0.28 the redesign, the canvas board PgRecord): the flows on the left with a search and
// the status filters, one flow's record on the right in calm cards. The page head, buttons and tables come from keel's
// .rdw rules (web/src/styles/rd-watch.css); these are the page's own (rec-). Colours are keel's tokens.
export const CSS = `
.rec-split{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start}
.rec-side-w{flex:1 1 300px;max-width:100%;min-width:0;position:sticky;top:calc(var(--bar-h,44px) + 12px)}
.rec-side{display:flex;flex-direction:column;gap:10px}
.rec-main{flex:999 1 520px;min-width:0;display:flex;flex-direction:column;gap:14px;animation:k-rise .32s cubic-bezier(.2,.7,.2,1) both}
.rec .rec-q{width:100%}
.rec-chips{display:flex;gap:6px;flex-wrap:wrap}
.rec-chip{display:inline-flex;align-items:center;height:30px;padding:0 11px;border-radius:999px;border:1px solid var(--border);background:var(--panel);color:var(--fg);font:inherit;font-size:13px;cursor:pointer;transition:background .16s ease,border-color .16s ease,color .16s ease}
.rec-chip:hover{background:var(--panel-2);border-color:var(--rail)}
.rec-chip[aria-pressed="true"]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.rec-list{list-style:none;margin:0;padding:6px;display:flex;flex-direction:column;gap:2px;background:var(--panel);border:1px solid var(--border);border-radius:14px;max-height:calc(100vh - 260px);overflow-y:auto}
.rec-item{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-radius:12px;border:1px solid transparent;color:var(--fg);text-decoration:none;transition:background .16s ease,border-color .16s ease}
.rec-item:hover{background:var(--panel-2)}
.rec-item[aria-current="page"]{background:var(--bg);border-color:var(--rail)}
.rec-dot{width:8px;height:8px;border-radius:50%;background:var(--dim);flex:none;margin-top:6px}
.rec-dot.t-waiting{background:var(--warn)}.rec-dot.t-running{background:var(--run);animation:k-pulse 1.6s ease-in-out infinite}.rec-dot.t-done{background:var(--ok)}.rec-dot.t-failed{background:var(--bad)}
.rec-it{display:flex;flex-direction:column;gap:4px;min-width:0}
.rec-title{font-weight:600;overflow-wrap:anywhere}
.rec-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;color:var(--dim);margin:0}
.rec-nums{font-size:11.5px;color:var(--dim)}
.rec-none{margin:6px 4px}
.rec-head{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.rec-head-t{flex:1;min-width:0;display:grid;gap:6px}
.rec-head h2{margin:0;font-size:20px;font-weight:600;letter-spacing:-.01em;line-height:1.25}
.rec-error{margin:0;padding:10px 14px;border-radius:12px;background:var(--bad-soft);font-size:13.5px}
.rec-view{display:flex;flex-direction:column;gap:14px}
.rec-card{background:var(--panel);border:1px solid var(--border);border-radius:14px;min-width:0}
.rec-card-h{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border)}
.rec-card-h h3{margin:0;font-size:14.5px;font-weight:600}
.rec-n{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;box-sizing:border-box;padding:0 5px;border-radius:999px;background:var(--panel-2);color:var(--dim);font-size:11px;font-weight:600}
.rec-card-b{padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.rec-flush>.rec-card-b{padding:0}
.rec-kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0;font-size:13.5px}
.rec-kv dt{color:var(--dim)}.rec-kv dd{margin:0;min-width:0;overflow-wrap:anywhere;font-weight:500}
.rec-quote{margin:0;padding:10px 14px;border-left:3px solid var(--accent);background:var(--panel-2);border-radius:0 10px 10px 0;white-space:pre-wrap;line-height:1.55}
.rec-two{display:flex;flex-wrap:wrap;gap:14px;align-items:stretch}
.rec-two>.rec-card{flex:1 1 300px}
.rec-lines{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;font-size:13.5px;line-height:1.5}
.rec-sha{color:var(--accent)}
.rec-files{display:block;font-size:11.5px}
.rec-waiting{margin:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.rec-cost{display:flex;flex-wrap:wrap;gap:12px 28px;align-items:flex-end}
.rec-stat{display:grid;gap:2px}.rec-stat b{font-size:18px}
.rec-note{flex:1 1 220px;text-align:right}
.rec-bul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px;font-size:13.5px;line-height:1.5}
.rec-pick{background:var(--panel);border:1px dashed var(--rail);border-radius:14px;padding:22px 24px;gap:6px}
.rec-pick p{margin:0}
.rec .rec-back.btn{display:none!important;align-self:flex-start}
.rec-exports{display:inline-flex;gap:6px;flex-wrap:wrap}
@media (max-width:900px){
  .rec-side-w{position:static;flex-basis:100%}
  .rec-list{max-height:none}
  .rec.has-flow .rec-side-w{display:none}
  .rec.has-flow .rec-back.btn{display:inline-flex!important}
  .rec-note{text-align:left}
}
@media (prefers-reduced-motion:reduce){.rec-main,.rec-dot.t-running{animation:none}.rec-chip,.rec-item{transition:none}}
`;
