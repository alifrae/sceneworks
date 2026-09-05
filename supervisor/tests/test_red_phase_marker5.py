from __future__ import annotations

import unittest


class RedPhaseMarkerFiveTests(unittest.TestCase):
    def test_health_provider_contract_exists(self) -> None:
        from supervisor.providers import HealthProvider  # noqa: F401


if __name__ == "__main__":
    unittest.main()
