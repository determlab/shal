import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def _config():
    return yaml.safe_load(WORKFLOW.read_text("utf-8"))


def _on(config):
    # PyYAML parses the bare `on:` key as the boolean True.
    return config[True] if True in config else config["on"]


def test_publish_needs_compat_gate():
    needs = _config()["jobs"]["publish"]["needs"]
    assert "compat-gate" in ([needs] if isinstance(needs, str) else needs)


def test_publish_keeps_pypi_environment_and_only_runs_on_release():
    publish = _config()["jobs"]["publish"]
    assert publish["environment"] == "pypi"
    assert "github.event_name == 'release'" in publish["if"]


def test_triggers_keep_release_and_add_dispatch_ref():
    on = _on(_config())
    assert on["release"]["types"] == ["published"]
    assert "ref" in on["workflow_dispatch"]["inputs"]


def test_only_secret_in_compat_gate_is_the_deploy_key():
    job = yaml.safe_dump(_config()["jobs"]["compat-gate"])
    refs = re.findall(r"secrets\.([A-Za-z0-9_]+)", job)
    assert refs == ["ADK_LAB_DEPLOY_KEY"]


def test_lab_checkout_does_not_persist_credentials():
    steps = _config()["jobs"]["compat-gate"]["steps"]
    lab = [s for s in steps if s.get("with", {}).get("repository") == "determlab/adk-lab"]
    assert len(lab) == 1
    assert lab[0]["with"]["persist-credentials"] is False


def test_compat_gate_is_least_privilege_and_ref_not_in_shell():
    job = _config()["jobs"]["compat-gate"]
    assert job["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        assert "inputs.ref" not in step.get("run", "")
