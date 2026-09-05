from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "start-sceneworks.ps1"


class LauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = LAUNCHER.read_text(encoding="utf-8")

    def test_launcher_bootstraps_out_of_process_supervisor(self) -> None:
        self.assertIn('http://127.0.0.1:8020', self.text)
        self.assertIn('-m supervisor', self.text)
        self.assertIn('SceneWorks Supervisor', self.text)

    def test_launcher_delegates_restart_and_reconcile_to_supervisor(self) -> None:
        self.assertIn('Invoke-SupervisorAction', self.text)
        self.assertIn('restart-all', self.text)
        self.assertIn('reconcile', self.text)
        self.assertNotIn('function Restart-SceneWorksStack', self.text)

    def test_launcher_keeps_supervisor_token_server_side(self) -> None:
        self.assertIn('Authorization', self.text)
        self.assertIn('Bearer', self.text)
        self.assertIn('X-SceneWorks-Actor', self.text)
        self.assertNotIn('Start-Process $SupervisorToken', self.text)


if __name__ == "__main__":
    unittest.main()
