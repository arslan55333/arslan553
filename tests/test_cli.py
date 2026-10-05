import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from leadengine import cli
from leadengine.service import DiscoverOutcome, LeadService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    shutil.copy(PROJECT_ROOT / "config.toml", tmp_path / "config.toml")
    monkeypatch.setenv("LEADENGINE_HOME", str(tmp_path))
    for key in ("SERPAPI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def invoke(*args):
    return runner.invoke(cli.app, list(args), env={"COLUMNS": "160"})


def test_every_command_has_help(home):
    out = invoke("--help")
    assert out.exit_code == 0
    names = [c.name or c.callback.__name__ for c in cli.app.registered_commands]
    for name in names:
        r = invoke(name.replace("_", "-"), "--help")
        assert r.exit_code == 0, (name, r.output)
    for sub in ("export", "push", "approve", "outbox", "send", "replies", "unsubscribe"):
        assert invoke("outreach", sub, "--help").exit_code == 0


def test_basic_commands_on_empty_database(home):
    assert invoke("init").exit_code == 0
    assert invoke("stats").exit_code == 0
    assert invoke("leads").exit_code == 0
    assert invoke("jobs").exit_code == 0
    assert "invalid" in invoke("zip", "123").output.lower() or invoke("zip", "123").exit_code != 0
    assert "Dallas" in invoke("zip", "75201").output
    r = invoke("backup")
    assert r.exit_code == 0 and list((home / "data" / "backups").glob("leadengine-*.db"))
    assert invoke("prune").exit_code == 0
    assert invoke("export", str(home / "out.xlsx")).exit_code == 0 and (home / "out.xlsx").exists()
    assert "nothing approved" in invoke("outreach", "outbox").output or invoke("outreach", "outbox").exit_code == 0
    r = invoke("outreach", "send", "--yes")
    assert r.exit_code == 1 and "sending is off" in r.output


def test_scan_city_zips_and_resume_after_interrupt(home, monkeypatch):
    calls = []
    state = {"crash": True}

    async def fake_discover(self, keyword, zip_code, **kw):
        if zip_code == "75204" and state["crash"]:
            state["crash"] = False
            raise KeyboardInterrupt                # user pressed Ctrl+C / PC went to sleep mid-run
        calls.append(zip_code)
        return DiscoverOutcome(1, "playwright", zip_code, False, [], sponsored=0)

    monkeypatch.setattr(LeadService, "discover", fake_discover)
    r = invoke("scan", "septic", "--zip", "75201, 75204", "--zip", "75206")
    assert r.exit_code == 130 and "--resume 1" in r.output
    assert calls == ["75201"]
    r = invoke("scan", "--resume", "1")
    assert r.exit_code == 0, r.output
    assert calls == ["75201", "75204", "75206"] and "already finished earlier" in r.output
    assert "Job #1 done" in r.output
    assert "already finished" in invoke("scan", "--resume", "1").output

    assert cli._scan_zips(None, None, "Dallas, TX", None, 0, 5) and len(cli._scan_zips(None, None, "Dallas, TX",
                                                                                     None, 0, 5)) == 5
    near = cli._scan_zips(None, None, None, "75201", 3, 100)
    assert near[0] == "75201" and len(near) > 3
