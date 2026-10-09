"""The parts keel brings itself, by module: keel_engine/extensions.py imports each one and reads its PART dict.

This is the one place core names a part (tests/test_fence.py). In step 3 each line moved into a plugin folder
(docs/plugins/11-step3-contract.md): plugins/map, plugins/ci, plugins/db, plugins/git, plugins/graph and plugins/keelbot
are loaded as add-ons. None is left here. The order is the registry's order, after each part's `order`
(keel_engine/extensions.py): Database (order 10), Git (20), the code graph (90, so a scan indexes the code before a
plugin's map), then the others (CI/CD, KeelBot).
"""

BUILTINS: tuple[str, ...] = ()
