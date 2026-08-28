export default function Loading() {
  return (
    <div className="loading-shell" aria-live="polite" aria-busy="true">
      <div className="loading-panel panel">
        <div className="loading-line short" />
        <div className="loading-line" />
        <div className="loading-line" />
      </div>
    </div>
  );
}
