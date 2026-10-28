"""Which MCP tools exist at which level (read, write, admin), which ones ask first, and how the command line picks the level."""
from __future__ import annotations

import os

from . import extensions

LEVELS = ("read", "write", "admin")
RANK = {"read": 0, "write": 1, "admin": 2}
# Tools that change something hard to undo: the client asks the person first (MCP elicitation), or with --yes not.
# A part's acting tool says so itself (PartServer.act confirm=True: keel_git_push, keel_db_change, as in 0.16.1).
CONFIRM = {"keel_delete_run", "keel_stop_flow", "keel_pause", "keel_stop_flows", "keel_remove_project", "keel_edit_workflow", "keel_restore_doc",
           "keel_rule_edit", "keel_keelbot_keep", "keel_keelbot_undo",
           "keel_workflow_create", "keel_rule_create", "keel_agent_create", "keel_agent_model",
           "keel_skill_create", "keel_skill_import", "keel_skill_attach", "keel_skill_market_install"}
CONFIRM_ACTIONS = {"open_pr", "pr"}        # keel_run_action actions that publish something: the person is asked first


def tool_level(name: str) -> str | None:
    """The level a tool is registered at (None: not a keel tool): keel's own (TOOLS), a part's (part_tools), the
    marketplace's (read)."""
    for lv, names in TOOLS.items():
        if name in names:
            return lv
    if name in MARKET_TOOLS:
        return "read"
    for lv, names in part_tools().items():
        if name in names:
            return lv
    return None


class _Names:
    """A stand-in server that only notes the names a part's keel_mcp registers (part_tools)."""

    def __init__(self):
        self.names: list[str] = []

    def tool(self, *_a, **_kw):
        def deco(fn):
            self.names.append(fn.__name__)
            return fn
        return deco


def part_tools() -> dict[str, list[str]]:
    """The parts' tools in keel's MCP server by level: their read tools, and their acting ones (write)."""
    out: dict[str, list[str]] = {}
    for lv, write in (("read", False), ("write", True)):
        rec = _Names()
        extensions.keel_mcp(rec, None, None, write=write)
        out[lv] = rec.names
    return out


# keel's own tools by level, in 0.16.1's order. The parts' tools (Database, Git, CI/CD) come from the parts, in 0.16.1's
# places: their read tools after keel_dashboard, their acting ones after keel_restore_doc (part_tools, build_server).
TOOLS: dict[str, tuple[str, ...]] = {
    "read": ("keel_status", "keel_read_gate", "keel_projects", "keel_timeline", "keel_next", "keel_explain", "keel_dashboard",
             "keel_flows", "keel_inbox", "keel_live", "keel_budget", "keel_specs", "keel_knowledge", "keel_search",
             "keel_read_file", "keel_actions", "keel_run_action", "keel_rules",
             # 0.18.64 a file of another registered project, read only, under an agent's read rules
             "keel_read_project_file",
             # 0.18.5 what keel TUI shows: the workflows, the agents, one agent, KeelBot's chats and changes, diagrams
             "keel_workflows", "keel_agents", "keel_agent", "keel_keelbot_chats", "keel_keelbot_changes", "keel_diagram",
             # 0.18.5 a new workflow, rule pack or agent: the format, a check that saves nothing, the agents there are
             "keel_format", "keel_workflow_check", "keel_rule_check", "keel_agent_definitions",
             # 0.18.6 skills: the project's (and which agents use each), one skill's text
             "keel_skills", "keel_skill",
             # 0.18.9 stacks: the project's, one with its YAML, what keel reads for a new one, a check that saves nothing
             "keel_stacks", "keel_stack", "keel_stack_suggest", "keel_stack_check",
             # 0.18.64 keel's spec check and citation check on a file of the project (they change nothing)
             "keel_spec_check", "keel_citations_check",
             # 0.18.64 a flow's document from its file (with `text` it replaces it: that needs the write level)
             "keel_flow_document",
             # 0.18.64 fetch every remote (a fetch changes no branch): one result per remote, the base's distance
             "keel_fetch_remotes",
             # 0.18.64 what really happened (an agent's prompt, what a retry changed) and the connection itself
             "keel_agent_prompt", "keel_flow_diff", "keel_health", "keel_reconnect",
             # 0.18.64 every provider's models and the efforts each takes, and which modes are set up
             "keel_models",
             # 0.18.64 keel's flow state, keel v1's leftover .keel/state.json too (its close needs --write)
             "keel_state"),
    "write": ("keel_approve_gate", "keel_resume", "keel_start_flow", "keel_answer", "keel_edit_gate", "keel_restore_doc",
              # 0.18.5 what keel TUI does: stop flows (asks first), a run mode, go on with a stopped flow, start an
              # agent, ask KeelBot, keep or undo its changes (asks first)
              "keel_stop_flow", "keel_stop_flows", "keel_run_mode", "keel_resume_flow", "keel_agent_start",
              "keel_keelbot_ask", "keel_keelbot_keep", "keel_keelbot_undo",
              # 0.18.5 save a new one (each asks the person first)
              "keel_workflow_create", "keel_rule_create", "keel_agent_create",
              # 0.18.6 a new skill (written here, from a link, from the marketplace) and which agents load it (each asks first)
              "keel_skill_create", "keel_skill_import", "keel_skill_attach", "keel_skill_market_install",
              # 0.18.9 an agent's or KeelBot's model and thinking effort (asks first)
              "keel_agent_model",
              # 0.18.9 a new stack, one removed, one turned off or on, one from the marketplace
              "keel_stack_create", "keel_stack_remove", "keel_stack_off", "keel_stack_market_install",
              # 0.18.64 the clarify result's send-back with a replacement draft, and its hand-over
              "keel_reject_with_document", "keel_handover",
              # 0.18.64 keel merges or rebases a flow's branch onto a base; a conflict stops with the files
              "keel_branch_sync"),
    "admin": ("keel_delete_run", "keel_add_project", "keel_remove_project",
              # 0.19.x what the dashboard and keel TUI do with projects: pause a flow, switch the focused project
              "keel_pause", "keel_focus",
              # 0.18.30 connect GitHub with a one-time code (the token is never returned)
              "keel_connect_github",
              "keel_edit_workflow", "keel_rule_install", "keel_rule_edit"),
}
# the marketplace's tools (step 4), after every tool keel 0.16.1 lists, at every level; keel_plugin_request only asks
MARKET_TOOLS = ("keel_marketplace_search", "keel_plugin_info", "keel_plugins_installed", "keel_plugin_request")


def level(argv: list[str]) -> str:
    """read, write or admin. --read-only always wins (agents get it), so an env setting cannot give an agent more."""
    if "--read-only" in argv:
        return "read"
    if "--admin" in argv or os.environ.get("KEEL_MCP_LEVEL") == "admin":
        return "admin"
    if "--write" in argv or os.environ.get("KEEL_MCP_WRITE", "0") == "1" or os.environ.get("KEEL_MCP_LEVEL") == "write":
        return "write"
    return "read"


def write_mode(argv: list[str]) -> bool:
    return level(argv) != "read"


def allowed(argv: list[str]) -> set[str] | None:
    """--allow t1,t2 (or KEEL_MCP_ALLOW): only these tools. An unknown name is an error, so a typo never hides a tool."""
    text = None
    for i, a in enumerate(argv):
        if a == "--allow" and i + 1 < len(argv):
            text = argv[i + 1]
        elif a.startswith("--allow="):
            text = a.split("=", 1)[1]
    text = text if text is not None else os.environ.get("KEEL_MCP_ALLOW")
    if not text:
        return None
    names = {t.strip() for t in text.split(",") if t.strip()}
    unknown = sorted(n for n in names if tool_level(n) is None)
    if unknown:
        every = {n for v in TOOLS.values() for n in v} | set(MARKET_TOOLS) | {n for v in part_tools().values() for n in v}
        raise SystemExit(f"keel mcp: no tool called {', '.join(unknown)}. Tools: {', '.join(sorted(every))}")
    return names
