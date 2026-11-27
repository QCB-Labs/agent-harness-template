import json

from tools import tool_sheet as t


def put(root, server, name, props, required=(), description="Does a thing. More detail."):
    folder = root / server
    folder.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "description": description,
            "parameters": {"properties": props, "required": list(required), "type": "object"}}
    (folder / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def test_lists_each_tool_with_required_and_optional_arguments(tmp_path):
    put(tmp_path, "harness", "read_drafts", {"slug": {}, "body_only": {}, "_conversationId": {}}, required=["slug"])
    text = t.build(tmp_path, ["harness"])
    assert "- `read_drafts(slug, body_only?)` - Does a thing" in text
    assert "_conversationId" not in text


def test_only_the_named_servers_appear_and_the_order_is_stable(tmp_path):
    put(tmp_path, "city", "b_tool", {})
    put(tmp_path, "city", "a_tool", {})
    put(tmp_path, "other", "x_tool", {})
    text = t.build(tmp_path, ["city"])
    assert "x_tool" not in text
    assert text.index("a_tool") < text.index("b_tool")
    assert text == t.build(tmp_path, ["city"])


def test_files_that_are_not_tools_are_skipped(tmp_path):
    put(tmp_path, "harness", "ok", {})
    (tmp_path / "harness" / "junk.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "harness" / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert t.build(tmp_path, ["harness"]).count("- `") == 1


def test_a_long_description_is_cut_to_one_short_line():
    note = t._first_sentence("word " * 60)
    assert len(note) <= t.MAX_NOTE and "\n" not in note
