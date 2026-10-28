"""keel 0.18.63 what KeelBot is told, from a chat that went round in circles: asked why Sonar failed and to "check the
code yourself and fix it", it asked for the Sonar report again and again; its "I will switch to Edit mode" option
switched nothing; a TLS handshake timeout ended the work; and it answered "The person asked for ...".
"""

from pathlib import Path

from keel_plugin_keelbot.chat import prompt

AGENT = (Path(prompt.__file__).parents[3] / "content" / "agents" / "helper.md").read_text()


def test_ask_mode_offers_edit_as_an_option_that_switches_the_mode():
    ask = prompt.MODE_TEXT["ask"]
    assert '"mode": "edit"' in ask
    assert "switches nothing" in ask and "do the work they asked for in that turn" in ask
    assert "after they switch, they ask again" not in ask
    assert '"mode": "edit"' in AGENT


def test_a_network_error_is_tried_once_more():
    assert "TLS handshake timeout" in prompt.FAILURES and "once more" in prompt.FAILURES
    assert "TLS handshake timeout" in AGENT


def test_without_the_tools_report_it_reviews_the_code_itself():
    assert "cannot see its report" in AGENT
    assert "never a condition to start" in AGENT and "80%" in AGENT


def test_it_talks_to_the_person_as_you():
    assert 'Talk to the person as "you"' in AGENT and "The person asked for" in AGENT
