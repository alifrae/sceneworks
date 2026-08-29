import type { TaskVerificationView, VerificationCheck, VerificationStatus } from "@/lib/types";

function statusClass(status: VerificationStatus): string {
  if (status === "PASS") return "stage-completed";
  if (status === "FAIL") return "stage-failed";
  if (status === "UNVERIFIABLE") return "stage-needs_input";
  return "";
}

function CheckGroup({ title, rows }: { title: string; rows: VerificationCheck[] }) {
  if (!rows.length) return null;
  return (
    <div className="advanced-group">
      <div className="advanced-group-title">{title}</div>
      <div className="issue-list">
        {rows.map((row) => (
          <div className="issue-row" key={row.key}>
            <div className="issue-main">
              <div className="row" style={{ gap: 8, alignItems: "center" }}>
                <span className={`stage-badge ${statusClass(row.status)}`}>{row.status.replaceAll("_", " ")}</span>
                <strong>{row.key}</strong>
              </div>
              <div style={{ marginTop: 6 }}>{row.label}</div>
              {row.detail && <div className="small muted" style={{ marginTop: 4 }}>{row.detail}</div>}
              {row.evidence.length > 0 && (
                <div className="small muted" style={{ marginTop: 4 }}>
                  Evidence: {row.evidence.map((ref) => `${ref.label}${ref.id ? ` #${ref.id}` : ""}`).join(" · ")}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function VerificationPanel({ view }: { view: TaskVerificationView }) {
  const { verification, resolution } = view;
  return (
    <div className="result-summary">
      <div className="result-outcome">
        <span className={`stage-badge ${statusClass(verification.overall)}`}>{verification.overall}</span>
        <span className="muted small">
          {verification.summary.pass} passed · {verification.summary.fail} failed · {verification.summary.unverifiable} unverifiable
        </span>
      </div>

      <p className="small muted">{verification.authority_note}</p>

      {resolution && (
        <div className="panel" style={{ margin: 0 }}>
          <div className="result-section-title">Issue resolution</div>
          {resolution.root_cause && (
            <div style={{ marginBottom: 12 }}>
              <strong>Root cause</strong>
              <div>{resolution.root_cause.text}</div>
              <div className="small muted">Attributed {resolution.root_cause.authority.replaceAll("_", " ")}</div>
            </div>
          )}
          {resolution.change_made && (
            <div style={{ marginBottom: 12 }}>
              <strong>Change made</strong>
              <div>{resolution.change_made.text}</div>
            </div>
          )}
          <div className="small">
            <strong>Resolved commit:</strong>{" "}
            <code>{resolution.resolved_commit?.slice(0, 12) || "—"}</code>
            {resolution.changed_files.length > 0 && <> · <strong>Files:</strong> {resolution.changed_files.join(", ")}</>}
          </div>
          {resolution.remaining_risk && (
            <div style={{ marginTop: 12 }}>
              <strong>Remaining risk</strong>
              <div>{resolution.remaining_risk.text}</div>
              <div className="small muted">Attributed {resolution.remaining_risk.authority.replaceAll("_", " ")}</div>
            </div>
          )}
        </div>
      )}

      <CheckGroup title="Acceptance criteria" rows={verification.acceptance_criteria} />
      <CheckGroup title="Required tests" rows={verification.required_tests} />
      <CheckGroup title="Scope / policy" rows={[...verification.scope, ...verification.policy]} />
      <CheckGroup title="Independent review" rows={[verification.reviewer]} />
    </div>
  );
}
