from __future__ import annotations

import unittest


class RedPhaseMarkerSixTests(unittest.TestCase):
    def test_fake_provider_contract_exists(self) -> None:
        from supervisor.providers import FakeProcessProvider  # noqa: F401


if __name__ == "__main__":
    unittest.main()
