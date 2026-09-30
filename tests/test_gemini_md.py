import json

from typer.testing import CliRunner

from agflow.cli import app
from agflow.gemini_md import analyze
from tests.conftest import write

BIG = ("# My rules\n- be nice\n\n## Python style\n" + "x" * 9000 + "\n\n## Deploy notes\n```sh\n" + "y" * 8000
       + "\n```\n\n## Python style\nagain\n\n<!-- hermes:begin -->\n## Hermes\n" + "z" * 9000
       + "\n<!-- hermes:end -->\n")


def test_analyze_breakdown():
    r = analyze(BIG)
    assert r["chars"] == len(BIG) and r["over_by"] == len(BIG) - 12_000
    assert r["blocks"][0]["name"] == "hermes"
    assert r["sections"][0]["title"] in {"Python style", "Hermes"}
    tips = " ".join(r["advice"])
    assert "Cut at least" in tips and "code blocks" in tips and "'python style'" in tips


def test_small_file_is_fine():
    assert analyze("# a\nshort\n")["advice"] == ["Within the limit; nothing to trim."]


def test_cli_is_read_only(home):
    f = write(home / ".gemini" / "GEMINI.md", BIG)
    before = f.read_text()
    r = CliRunner().invoke(app, ["gemini-md", "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["over_by"] > 0
    assert CliRunner().invoke(app, ["gemini-md"]).exit_code == 0
    assert f.read_text() == before
