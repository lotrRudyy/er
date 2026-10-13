import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_er1.py"
SPEC = importlib.util.spec_from_file_location("update_er1", SCRIPT)
assert SPEC and SPEC.loader
UPDATE_ER1 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = UPDATE_ER1
SPEC.loader.exec_module(UPDATE_ER1)


class UpdateEr1Tests(unittest.TestCase):
    def test_maps_only_runtime_files(self):
        cases = {
            "scripts/game_master/game_master.py": "scripts/game_master/game_master.py",
            "scripts/game_master/requirements.txt": "scripts/game_master/requirements.txt",
            "scripts/game_master/README.md": None,
            "web/er1_dashboard/app.py": "web/app.py",
            "web/er1_dashboard/log_archive.py": "web/log_archive.py",
            "web/er1_dashboard/static/app.js": "web/static/app.js",
            "web/er1_dashboard/static/app.js.bak": None,
            "web/er1_dashboard/static/app.js.orig": None,
            "web/er1_dashboard/templates/index.html": "web/templates/index.html",
            "web/er1_dashboard/templates/index.html.bak": None,
            "web/er1_dashboard/.env.example": None,
            "web/er1_dashboard/dashboard_hints.json": None,
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(UPDATE_ER1.map_repo_path(source), expected)

    def test_update_plan_tracks_writes_and_deletes(self):
        managed = UPDATE_ER1.ManagedFile
        base = {
            "scripts/game_master/a.py": managed(
                "scripts/game_master/a.py", "scripts/game_master/a.py", "old-a"
            ),
            "web/er1_dashboard/static/old.js": managed(
                "web/er1_dashboard/static/old.js", "web/static/old.js", "old-js"
            ),
        }
        head = {
            "scripts/game_master/a.py": managed(
                "scripts/game_master/a.py", "scripts/game_master/a.py", "new-a"
            ),
            "web/er1_dashboard/static/new.js": managed(
                "web/er1_dashboard/static/new.js", "web/static/new.js", "new-js"
            ),
        }

        actions = UPDATE_ER1.build_update_plan(base, head)

        self.assertEqual(
            [(item.action, item.remote_path) for item in actions],
            [
                ("write", "scripts/game_master/a.py"),
                ("write", "web/static/new.js"),
                ("delete", "web/static/old.js"),
            ],
        )

    def test_idle_gate_rejects_active_game(self):
        active = {
            "game": {
                "phase": 3,
                "timer_running": True,
                "is_live": True,
                "run_id": "run-1",
            },
            "meta": {"persistence_degraded": False},
        }
        with self.assertRaises(UPDATE_ER1.UpdateError):
            UPDATE_ER1.assert_dashboard_idle_state(active)

    def test_idle_gate_rejects_incomplete_dashboard_state(self):
        incomplete = {
            "game": {"phase": 0},
            "meta": {"persistence_degraded": False},
        }
        with self.assertRaises(UPDATE_ER1.UpdateError):
            UPDATE_ER1.assert_dashboard_idle_state(incomplete)

    def test_idle_gate_accepts_healthy_standby(self):
        standby = {
            "game": {
                "phase": 0,
                "timer_running": False,
                "is_live": False,
                "run_id": "",
            },
            "meta": {"persistence_degraded": False},
        }
        UPDATE_ER1.assert_dashboard_idle_state(standby)

    def test_authoritative_idle_gate_rejects_live_run(self):
        live = {
            "phase": 12,
            "timer_running": True,
            "run": {"run_id": "run-1"},
            "recovery": {"durability_degraded": False},
        }
        with self.assertRaises(UPDATE_ER1.UpdateError):
            UPDATE_ER1.assert_game_master_idle_state(live)

    def test_authoritative_idle_gate_accepts_standby(self):
        standby = {
            "phase": 0,
            "timer_running": False,
            "run": None,
            "recovery": {"durability_degraded": False},
        }
        UPDATE_ER1.assert_game_master_idle_state(standby)

    def test_profile_exposes_update_command(self):
        profile = (ROOT / "scripts" / "er1_profile.ps1").read_text(encoding="utf-8")
        self.assertIn('"update" = "Safely update', profile)
        self.assertIn('"update" {', profile)
        self.assertIn("Invoke-Er1Update", profile)

    def test_profile_rejects_unbound_update_arguments(self):
        profile = ROOT / "scripts" / "er1_profile.ps1"
        command = (
            f". '{profile.as_posix()}'; "
            "$failedClosed = $false; "
            "try { er1 update --dry-run } "
            "catch { $failedClosed = $_.Exception.Message -like 'Usage: er1 update*' }; "
            "if (-not $failedClosed) { throw 'update argument did not fail closed' }"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_non_production_remote_root_is_rejected(self):
        with self.assertRaises(UPDATE_ER1.UpdateError):
            UPDATE_ER1.validate_destination(
                "rudyy@100.108.1.80", "/home/rudyy/er1-test"
            )


if __name__ == "__main__":
    unittest.main()
