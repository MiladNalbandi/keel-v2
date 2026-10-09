"""The Code Review plugin's engine part (plugins/review): its content (the Tools › Plugins entry and KeelBot's
/review-branch and /explain-pr commands, content/plugins/review/plugin.yml) and the fake model's answers for its runs
(fake.py: the overview, the findings and the check of findings that the api's ReviewAiService asks KeelBot for). The
review itself (pull requests, diffs, threads, Submit) is the plugin's api part; the engine has no actions or routes
for it.

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_review, written by `keel-engine plugins resolve`). It has no
PART: it never was a built-in part, and as a part with no keys it changes nothing in keel's flows or KeelBot's prompt.

    repo:    plugins/review/engine/keel_plugin_review/   ->  plugins/review/content
    plugin:  <plugin>/engine/keel_plugin_review/         ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from pathlib import Path

from .fake import answer as fake_answer

# the same version as ../../keel-plugin.yml (tests/test_review_part.py checks it)
VERSION = "1.0.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

ADDON = {
    "name": "review",
    "title": "Code Review",
    "version": VERSION,
    "content": CONTENT,
    "fake": fake_answer,
}
