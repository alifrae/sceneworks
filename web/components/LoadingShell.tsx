export default function LoadingShell({ title }: { title: string }) {
  return (
    <div className="loading-shell" aria-busy="true" aria-live="polite">
      <h1>{title}</h1>
      <div className="panel loading-panel">
        <div className="loading-line" />
        <div className="loading-line short" />
        <span className="muted small">Loading current data…</span>
      </div>
    </div>
  );
}
