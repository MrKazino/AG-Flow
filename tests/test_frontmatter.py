import pytest

from agflow import frontmatter


def test_parse_and_body():
    meta, body = frontmatter.parse("---\nname: x\ntools: [a, b]\nflag: true\n---\n# Title\nbody\n")
    assert meta == {"name": "x", "tools": ["a", "b"], "flag": True}
    assert body == "# Title\nbody\n"


def test_no_frontmatter():
    meta, body = frontmatter.parse("# Just markdown\n")
    assert meta == {}
    assert body == "# Just markdown\n"
    assert not frontmatter.has_frontmatter("# Just markdown\n")


def test_empty_frontmatter():
    meta, body = frontmatter.parse("---\n---\nbody")
    assert meta == {}
    assert body == "body"


def test_invalid_yaml_raises():
    with pytest.raises(frontmatter.FrontmatterError):
        frontmatter.parse("---\nname: [unclosed\n---\nx")


def test_non_mapping_raises():
    with pytest.raises(frontmatter.FrontmatterError):
        frontmatter.parse("---\n- a\n- b\n---\nx")


def test_update_preserves_comments_order_and_body():
    text = "---\n# keep me\nname: a\ndescription: d  # inline\nagents: [x, y]\n---\n\nBody stays.\n"
    out = frontmatter.update(text, {"agents": ["x"]}, remove=("description",))
    assert "# keep me" in out
    assert "description" not in out
    assert out.index("name") < out.index("agents")
    assert out.endswith("\nBody stays.\n")
    assert frontmatter.parse(out)[0]["agents"] == ["x"]


def test_dump_roundtrip():
    text = frontmatter.dump({"name": "n", "description": "d"}, "# Body\n")
    meta, body = frontmatter.parse(text)
    assert meta == {"name": "n", "description": "d"}
    assert body.strip() == "# Body"


def test_update_keeps_the_closing_fence_on_its_own_line():
    text = "---\nname: a\ndescription: d\n---\n# Title\nBody.\n"
    out = frontmatter.update(text, {"name": "b"})
    assert out == "---\nname: b\ndescription: d\n---\n# Title\nBody.\n"
    assert frontmatter.parse(out) == ({"name": "b", "description": "d"}, "# Title\nBody.\n")


def _count_yaml_loads(monkeypatch):
    calls: list[int] = []
    real = frontmatter.yaml_rt

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(frontmatter, "yaml_rt", counting)
    frontmatter._load_cached.cache_clear()
    return calls


def test_the_same_frontmatter_is_parsed_once(monkeypatch):
    calls = _count_yaml_loads(monkeypatch)
    text = "---\nname: parsed-once\ntools: [a, b]\n---\nbody"
    first = frontmatter.parse(text)
    second = frontmatter.parse(text)
    assert first == second == ({"name": "parsed-once", "tools": ["a", "b"]}, "body")
    assert len(calls) == 1


def test_a_caller_can_not_change_what_the_cache_holds():
    text = "---\nname: private-copy\ntools: [a]\nnested: {k: [1]}\n---\n"
    meta, _ = frontmatter.parse(text)
    meta["name"] = "changed"
    meta["tools"].append("b")
    meta["nested"]["k"].append(2)
    again, _ = frontmatter.parse(text)
    assert again == {"name": "private-copy", "tools": ["a"], "nested": {"k": [1]}}


def test_edited_frontmatter_of_the_same_length_is_not_served_from_the_cache():
    """The key is the text itself, not a file's name or timestamp: there is no way to read an old result."""
    first, _ = frontmatter.parse("---\nname: aaaa\n---\n")
    second, _ = frontmatter.parse("---\nname: bbbb\n---\n")
    assert (first["name"], second["name"]) == ("aaaa", "bbbb")


def test_broken_frontmatter_keeps_raising_without_being_parsed_again(monkeypatch):
    calls = _count_yaml_loads(monkeypatch)
    for text in ("---\nname: [unclosed\n---\nx", "---\n- a\n- b\n---\nx"):
        for _ in range(3):
            with pytest.raises(frontmatter.FrontmatterError):
                frontmatter.parse(text)
    assert len(calls) == 2


def test_long_frontmatter_is_parsed_every_time(monkeypatch):
    calls = _count_yaml_loads(monkeypatch)
    text = "---\ndescription: " + "x" * (frontmatter.MAX_CACHED_CHARS + 10) + "\n---\n"
    assert frontmatter.parse(text) == frontmatter.parse(text)
    assert len(calls) == 2
