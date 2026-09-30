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
