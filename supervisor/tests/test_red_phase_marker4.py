from __future__ import annotations

import unittest


class RedPhaseMarkerFourTests(unittest.TestCase):
    def test_lifecycle_error_contract_exists(self) -> None:
        from supervisor.core import LifecycleError  # noqa: F401


if __name__ == "__main__":
    unittest.main()
