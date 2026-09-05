from __future__ import annotations

import unittest


class SupervisorImportContractTests(unittest.TestCase):
    def test_supervisor_core_is_importable(self) -> None:
        from supervisor.core import LifecycleSupervisor  # noqa: F401


if __name__ == "__main__":
    unittest.main()
