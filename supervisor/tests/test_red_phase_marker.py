from __future__ import annotations

import unittest


class RedPhaseMarkerTests(unittest.TestCase):
    def test_model_contract_exists(self) -> None:
        from supervisor.model import ComponentKey  # noqa: F401


if __name__ == "__main__":
    unittest.main()
