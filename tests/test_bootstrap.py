from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from clifft_bench import bootstrap as setup
from clifft_bench.config import Implementation


@pytest.fixture
def campaign(tmp_path):
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments/selected.txt").write_text("example==1\n")
    selected = Implementation(
        {
            "id": "selected",
            "adapter": "fixture",
            "version": "1",
            "distribution": "example",
            "environment": {
                "import_modules": ["example"],
                "install_environment": {"NATIVE_BUILD": "1", "CUDA": "0"},
            },
        },
        tmp_path,
    )
    return SimpleNamespace(
        run={"classification": "smoke"}, cases=(SimpleNamespace(implementation=selected),) * 2
    )


def test_bootstrap_installs_only_selected_environments_and_verifies_them(campaign, monkeypatch):
    run = Mock()
    monkeypatch.setattr(setup.subprocess, "run", run)
    setup.bootstrap(campaign)
    calls = run.call_args_list
    assert len(calls) == 5  # One environment despite two selected variants.
    imp = campaign.cases[0].implementation
    assert calls[0].args[0][-1] == str(imp.environment_path)
    assert calls[2].args[0] == [
        imp.python_executable(),
        "-m",
        "pip",
        "install",
        "--no-deps",
        "-r",
        str(imp.requirements_path),
    ]
    assert calls[3].args[0][-3:] == ["-m", "pip", "check"]
    assert calls[4].args[0][1] == "-c"
    for call in calls[1:]:
        assert call.kwargs["check"] is True
        assert call.kwargs["env"]["NATIVE_BUILD"] == "1"
        assert call.kwargs["env"]["CUDA"] == "0"
    assert calls[4].kwargs["env"]["OMP_NUM_THREADS"] == "1"


def test_bootstrap_stops_on_dependency_failure(campaign, monkeypatch):
    run = Mock(side_effect=[None, None, None, setup.subprocess.CalledProcessError(1, "pip check")])
    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(setup.subprocess.CalledProcessError):
        setup.bootstrap(campaign)
    assert run.call_count == 4


def test_missing_lock_fails_before_installing(campaign, monkeypatch):
    campaign.cases[0].implementation.requirements_path.unlink()
    run = Mock()
    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(ValueError, match="missing requirements"):
        setup.bootstrap(campaign)
    run.assert_not_called()


def test_official_bootstrap_requires_clean_source_before_installing(campaign, monkeypatch):
    campaign.run["classification"] = "official"
    monkeypatch.setattr(setup, "require_clean_checkout",
                        Mock(side_effect=ValueError("checkout must be clean")))
    run = Mock()
    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(ValueError, match="checkout must be clean"):
        setup.bootstrap(campaign)
    run.assert_not_called()


@pytest.mark.parametrize("installed_version", ["1", "2"])
def test_identity_probe_executes_version_and_source_checks(
    campaign, monkeypatch, installed_version
):
    calls = Mock()
    monkeypatch.setattr(setup.subprocess, "run", calls)
    definition = campaign.cases[0].implementation.definition
    definition.update(commit_sha="a"*40, source_url="https://example.com/source")
    setup.verify_environment(campaign.cases[0].implementation)
    command = calls.call_args_list[-1].args[0]
    import importlib
    import importlib.metadata
    import sys

    from clifft_bench import adapters

    imported = Mock()
    adapter = Mock()
    monkeypatch.setattr(importlib, "import_module", imported)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: installed_version)
    monkeypatch.setattr(adapters, "load_adapter", lambda name: adapter)
    monkeypatch.setattr(sys, "argv", ["-c", command[-1]])
    if installed_version == "2":
        with pytest.raises(ValueError, match="expected 1, got 2"):
            exec(command[2], {})
        adapter.verify_installation.assert_not_called()
    else:
        exec(command[2], {})
        imported.assert_called_once_with("example")
        adapter.verify_installation.assert_called_once_with(
            expected_commit="a"*40, source_url="https://example.com/source")
