from __future__ import annotations

import unittest


class RedPhaseMarkerThreeTests(unittest.TestCase):
    def test_supervisor_state_contract_exists(self) -> None:
        from supervisor.model import ComponentState  # noqa: F401


if __name__ == "__main__":
    unittest.main()
