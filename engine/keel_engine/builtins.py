"""The parts keel brings itself, by module: keel_engine/extensions.py imports each one and reads its PART dict.

This is the one place core names a part (tests/test_fence.py). In step 3 each line moves into a plugin folder
(docs/plugins/11-step3-contract.md): plugins/map, plugins/ci, plugins/db and plugins/git left already (loaded as
add-ons, after these). The order is the registry's order, after each part's `order` (keel_engine/extensions.py): the
scan runs the code graph before a plugin's map; KeelBot hears about Database (order 10), Git (20), then the others
(CI/CD).
"""

BUILTINS = (
    "keel_engine.plugins.graph",       # the code graph: its index, MCP server and hints, /projects/{pid}/graph*
    "keel_engine.runtime.keelbot",     # KeelBot: its commits in the PR body
)
