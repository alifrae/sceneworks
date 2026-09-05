from __future__ import annotations

import unittest


class RedPhaseMarkerTwoTests(unittest.TestCase):
    def test_provider_contract_exists(self) -> None:
        from supervisor.providers import ProcessProvider  # noqa: F401


if __name__ == "__main__":
    unittest.main()
