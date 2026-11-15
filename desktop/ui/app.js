// keel desktop's own pages: they show what the app's main process says (keel-app://app/api/state) and send the
// person's choice back (POST keel-app://app/api/do/<action>). No Node here: the page is sandboxed like keel's own.
"use strict";
const view = document.getElementById("view");
const foot = document.getElementById("foot");
let last = "";

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "onclick") e.addEventListener("click", v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(typeof k === "string" ? document.createTextNode(k) : k);
  return e;
}

async function act(name, button) {
  if (button) button.disabled = true;
  try {
    await fetch(`keel-app://app/api/do/${name}`, { method: "POST" });
  } finally {
    if (button) setTimeout(() => (button.disabled = false), 800);
  }
}

function btn(label, name, primary) {
  const b = el("button", { type: "button", class: primary ? "primary" : null }, label);
  b.addEventListener("click", () => act(name, b));
  return b;
}

function render(s) {
  const out = [];
  switch (s.page) {
    case "welcome":
      out.push(
        el("h1", {}, "keel runs your coding agents, here on this computer"),
        el("p", { class: "lead" }, "Choose the folder of a project, or try keel on its demo project first. You can add more folders later (File › Add Project Folder)."),
        el("div", { class: "warn", role: "note" },
          el("strong", {}, "keel's agents run as you. "),
          "They can use your files, your SSH keys and your network. Use keel only with projects you trust."),
        el("div", { class: "btns" }, btn("Choose a project folder…", "choose-folder", true), btn("Try the demo first", "demo")),
        el("p", { class: "sub" }, "Your AI provider's API key goes into keel, under Connections: keel calls the provider's own API with it. keel's data stays in ", el("code", {}, s.data), "."),
      );
      break;
    case "git":
      out.push(
        el("h1", {}, "keel needs git"),
        el("p", { class: "lead" }, "Every flow works in a git repository, and git is not on this computer yet."),
        el("pre", {}, s.hint),
        el("div", { class: "btns" },
          s.canInstall ? btn("Install Apple's Command Line Tools", "install-clt", true) : null,
          btn("Check again", "retry", !s.canInstall)),
        s.canInstall ? el("p", { class: "sub" }, "Apple's installer opens. When it is done, choose Check again.") : null,
      );
      break;
    case "starting": {
      const secs = Math.max(0, Math.round((Date.now() - s.since) / 1000));
      out.push(
        el("p", { class: "lead" }, el("span", { class: "spin", "aria-hidden": "true" }), s.text),
        el("p", { class: "sub" }, secs < 20 ? "This takes about 15 seconds." : `${secs} seconds. The first start after an update takes longer.`),
      );
      break;
    }
    case "stopping":
      out.push(el("p", { class: "lead" }, el("span", { class: "spin", "aria-hidden": "true" }), s.text));
      break;
    case "failed":
      out.push(
        el("h1", {}, s.title || "keel could not start"),
        el("p", { class: "lead bad" }, s.why),
        s.log ? el("pre", { class: "log", tabindex: "0", "aria-label": "The last lines of keel's log" }, s.log) : null,
        el("div", { class: "btns" }, btn("Try again", "retry", true), btn("Start in safe mode", "safe"), btn("Open the logs", "logs"), btn("Copy a report", "copy-report")),
        el("p", { class: "sub" }, "Safe mode starts keel with only the plugins that came with it. The logs are in ", el("code", {}, s.logs), "."),
      );
      break;
    default:
      out.push(el("p", { class: "lead" }, el("span", { class: "spin", "aria-hidden": "true" }), s.text || "Opening keel…"));
  }
  view.replaceChildren(...out.filter(Boolean));
  const pre = view.querySelector("pre.log");
  if (pre) pre.scrollTop = pre.scrollHeight; // the last lines of the log are the ones that matter
  foot.textContent = `keel desktop ${s.version}`;
}

async function tick() {
  try {
    const r = await fetch("keel-app://app/api/state");
    const s = await r.json();
    const key = JSON.stringify(s) + (s.page === "starting" ? Math.floor(Date.now() / 5000) : "");
    if (key !== last) {
      last = key;
      render(s);
    }
  } catch {
    /* the app is busy: next time */
  }
  setTimeout(tick, 500);
}
tick();
