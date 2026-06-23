"""`.env` beside the topology resolves ${ENV} in the core — agent-agnostic (#73).

The core loads a sibling `.env` into the environment so `${ENV}` placeholders
resolve, without secrets ever touching a host config. Real env vars win.
"""
import logging
import os

import pytest

import shal
from shal import Driver, idempotent, op, register
from shal.loader import _apply_dotenv, _parse_dotenv


@register
class _DotenvKnob(Driver):
    compatible = "test,dotenv-knob"
    kind = None
    llm_ready = True

    def bind(self, node):
        super().bind(node)

    @idempotent
    @op("Read a value.", side_effect="none")
    def read(self) -> int:
        return 1


_TOPO = """
shal_version: 1
root:
  dev: {id: dotdev, driver: 'test,dotenv-knob', address: '${%s}'}
"""


def test_parse_dotenv_formats():
    out = _parse_dotenv('# comment\nexport A=1\nB="two words"\nC=\'q\'\n\nNOEQ\nD=ok#keep\n')
    assert out == {"A": "1", "B": "two words", "C": "q", "D": "ok#keep"}
    assert "NOEQ" not in out  # a line without '=' is skipped


def test_parse_dotenv_inline_comment():
    # an unquoted ` # comment` is dropped; a `#` with no leading space, or inside
    # quotes, stays literal (#86 — the cryptic getaddrinfo bug was the comment leaking).
    out = _parse_dotenv('HOST=h.example # prod\nQ="a # b"\nP=x#y\n')
    assert out == {"HOST": "h.example", "Q": "a # b", "P": "x#y"}


def test_dotenv_resolves_env_for_load(tmp_path):
    var = "DOTENV_ADDR_1"
    os.environ.pop(var, None)
    (tmp_path / ".env").write_text(f"{var}=10.0.0.5\n", encoding="utf-8")
    (tmp_path / "top.yaml").write_text(_TOPO % var, encoding="utf-8")
    try:
        with shal.load(str(tmp_path / "top.yaml")) as hal:
            assert hal.get_device("dotdev") is not None  # ${VAR} resolved from .env
    finally:
        os.environ.pop(var, None)


def test_missing_env_without_dotenv_raises(tmp_path):
    var = "DOTENV_ADDR_2"
    os.environ.pop(var, None)
    (tmp_path / "top.yaml").write_text(_TOPO % var, encoding="utf-8")  # no .env beside it
    with pytest.raises(shal.LoadError, match="not set"):
        shal.load(str(tmp_path / "top.yaml"))


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTENV_WINS", "from_real_env")
    (tmp_path / ".env").write_text("DOTENV_WINS=from_dotenv\n", encoding="utf-8")
    _apply_dotenv(tmp_path)
    assert os.environ["DOTENV_WINS"] == "from_real_env"  # deliberately-set var not clobbered


def test_warns_when_env_not_gitignored(tmp_path, caplog):
    var = "DOTENV_WARN"
    os.environ.pop(var, None)
    (tmp_path / ".git").mkdir()                 # repo root, no .gitignore -> .env not ignored
    (tmp_path / ".env").write_text(f"{var}=x\n", encoding="utf-8")
    try:
        with caplog.at_level(logging.WARNING, logger="shal.loader"):
            _apply_dotenv(tmp_path)
        assert any("gitignore" in r.message for r in caplog.records)
    finally:
        os.environ.pop(var, None)
