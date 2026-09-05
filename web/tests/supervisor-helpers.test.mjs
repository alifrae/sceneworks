import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeSupervisorStatus,
  validateSupervisorComponent,
} from "../lib/supervisor-public.mjs";

test("component validation accepts only semantic lifecycle keys", () => {
  for (const value of ["api", "web", "mcp_tunnel", "all"]) {
    assert.equal(validateSupervisorComponent(value), value);
  }
  assert.throws(() => validateSupervisorComponent("database"));
  assert.throws(() => validateSupervisorComponent({ pid: 123 }));
});

test("status normalization strips internal and unknown fields", () => {
  const result = normalizeSupervisorStatus({
    aggregate_state: "HEALTHY",
    token: "must-not-leak",
    components: {
      api: {
        state: "HEALTHY",
        restart_attempts: 1,
        consecutive_failures: 0,
        last_transition_at: 12,
        healthy_since: 10,
        enabled: true,
        pid: 999,
        command: "secret",
      },
      database: { state: "HEALTHY", pid: 5 },
    },
  });
  assert.deepEqual(Object.keys(result).sort(), ["aggregate_state", "components"]);
  assert.deepEqual(Object.keys(result.components), ["api"]);
  assert.equal(result.components.api.restart_attempts, 1);
  assert.equal("pid" in result.components.api, false);
  assert.equal("token" in result, false);
});
