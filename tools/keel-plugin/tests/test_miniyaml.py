"""The small YAML reader: what keel-plugin.yml and the marketplace files use, read as PyYAML's safe_load reads it."""

from __future__ import annotations

import math

import pytest

from keel_plugin.miniyaml import YamlError, loads


def test_a_manifest_like_the_plugins_in_keel():
    text = """\
# a comment
schema: 1
name: product
title: keel Product
version: 0.1.0-beta.1   # a comment after a value
summary: keel's chat about your project - it answers with file:line links.
requires:
  sdk: 1
  keel: ">=0.15.0,<1.0.0"
  # a comment inside a mapping
  plugins: { tasks: ">=1.0.0", jira: '>=1.0.0' }
parts:
  engine: { path: engine, package: keel_product }
  api: { jars: [api/keel-plugin-product.jar] }
  web: { entry: web/index.js, css: [web/style.css] }
  content: content
per_project: false
contributes:
  slots:
    - { slot: code.activity, id: db, title: Database }
    - slot: step.card
      match: "db:*"
  connections:
    - kind: database
      fields:
        [{ name: url, secret: true }, { name: env, choices: [dev, test, prod] }]
"""
    assert loads(text) == {
        "schema": 1, "name": "product", "title": "keel Product", "version": "0.1.0-beta.1",
        "summary": "keel's chat about your project - it answers with file:line links.",
        "requires": {"sdk": 1, "keel": ">=0.15.0,<1.0.0", "plugins": {"tasks": ">=1.0.0", "jira": ">=1.0.0"}},
        "parts": {"engine": {"path": "engine", "package": "keel_product"},
                  "api": {"jars": ["api/keel-plugin-product.jar"]},
                  "web": {"entry": "web/index.js", "css": ["web/style.css"]}, "content": "content"},
        "per_project": False,
        "contributes": {
            "slots": [{"slot": "code.activity", "id": "db", "title": "Database"},
                      {"slot": "step.card", "match": "db:*"}],
            "connections": [{"kind": "database", "fields": [
                {"name": "url", "secret": True}, {"name": "env", "choices": ["dev", "test", "prod"]}]}]},
    }


@pytest.mark.parametrize("word,value", [
    ("1", 1), ("-3", -3), ("1.0", 1.0), ("1.0.0", "1.0.0"), ("0x1f", 31), ("yes", True), ("Off", False),
    ("~", None), ("null", None), ("", None), ("2026-10-09", "2026-10-09"), ("RWQ+/=", "RWQ+/="),
    ("'it''s'", "it's"), ('"a\\tb\\u00e9"', "a\tb\u00e9"), ("https://x.y/z#a", "https://x.y/z#a"),
])
def test_plain_and_quoted_words(word, value):
    assert loads(f"k: {word}\n") == {"k": value}


def test_special_numbers():
    assert math.isinf(loads("k: .inf")["k"]) and math.isnan(loads("k: .nan")["k"])


def test_lists_and_empty_values():
    assert loads("a:\n- 1\n- two\nb: []\nc: {}\nd:\n") == {"a": [1, "two"], "b": [], "c": {}, "d": None}
    assert loads("- a\n-\n  - b\n- k: v\n  j: w\n") == ["a", ["b"], {"k": "v", "j": "w"}]


def test_block_text():
    text = "a: |\n  one\n    two\n\n  three\nb: >-\n  folded\n  line\n\n  next\nc: |+\n  keep\n\nd: 1\n"
    assert loads(text) == {"a": "one\n  two\n\nthree\n", "b": "folded line\nnext", "c": "keep\n\n", "d": 1}
    # folded next to a more indented line: the line breaks stay
    assert loads("a: >\n  one\n  two\n\n  three\n    indented\n  four\n") == {"a": "one two\nthree\n  indented\nfour\n"}


def test_plain_and_quoted_text_over_lines():
    assert loads("a: one\n  two\n  three\nb: 2\n") == {"a": "one two three", "b": 2}
    assert loads('a: "one\n  two"\n') == {"a": "one two"}
    assert loads("a: [1,\n  2]\n") == {"a": [1, 2]}


def test_empty_documents():
    assert loads("") is None and loads("# only a comment\n---\n") is None


@pytest.mark.parametrize("text,why", [
    ("a: &x 1\nb: *x\n", "anchors"),
    ("a: !!str 1\n", "tags"),
    ("a:\n\tb: 1\n", "tabs"),
    ("a: [1, 2\n", "not closed"),
    ("a:\n  b: {x: 1}\n    c: 2\n", "indented more"),
    ("---\na: 1\n---\nb: 2\n", "one YAML document"),
    ("a: 1\n  b: 2\n", "look like 'key: value'"),
])
def test_what_it_does_not_read_is_named_with_its_line(text, why):
    with pytest.raises(YamlError, match=why):
        loads(text)
