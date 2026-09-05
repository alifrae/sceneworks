export const SUPERVISOR_COMPONENTS = ["api", "web", "mcp_tunnel", "all"];
const STATUS_COMPONENTS = ["api", "web", "mcp_tunnel"];

export function validateSupervisorComponent(value) {
  if (!SUPERVISOR_COMPONENTS.includes(value)) {
    throw new Error("component must be api, web, mcp_tunnel, or all");
  }
  return value;
}

export function normalizeSupervisorStatus(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const rawComponents = source.components && typeof source.components === "object" ? source.components : {};
  const components = {};
  for (const key of STATUS_COMPONENTS) {
    const raw = rawComponents[key];
    if (!raw || typeof raw !== "object") continue;
    components[key] = {
      state: String(raw.state || "UNKNOWN"),
      consecutive_failures: Number.isFinite(raw.consecutive_failures) ? Number(raw.consecutive_failures) : 0,
      restart_attempts: Number.isFinite(raw.restart_attempts) ? Number(raw.restart_attempts) : 0,
      last_transition_at: Number.isFinite(raw.last_transition_at) ? Number(raw.last_transition_at) : null,
      healthy_since: Number.isFinite(raw.healthy_since) ? Number(raw.healthy_since) : null,
      enabled: raw.enabled !== false,
    };
  }
  return {
    aggregate_state: String(source.aggregate_state || "UNKNOWN"),
    components,
  };
}
