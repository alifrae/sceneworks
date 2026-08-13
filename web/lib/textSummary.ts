// Small text helpers for the Work Thread's summary views. No fabrication:
// every value here is extracted from real backend text (markdown result
// blobs, `git diff --stat` output), never invented.

export function firstParagraph(text: string, maxLen = 420): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const withoutHeading = trimmed.replace(/^#{1,4}\s+.*\n+/, "");
  const para = withoutHeading.split(/\n\s*\n/)[0] || withoutHeading;
  const oneLine = para.replace(/\s+/g, " ").trim();
  return oneLine.length > maxLen ? `${oneLine.slice(0, maxLen).trim()}…` : oneLine;
}

export function filesChangedCount(stat: string): number | null {
  const match = stat.match(/(\d+) files? changed/);
  return match ? Number(match[1]) : null;
}

/**
 * Mirrors backend/app/services/workflow.py:parse_review_verdict exactly.
 * The label shown here must agree with what the workflow actually decided —
 * an independent heuristic (e.g. "contains the word APPROVED") can disagree
 * with the backend's real parse and show the wrong verdict.
 */
export function reviewVerdictLabel(reviewResult: string | null | undefined): string | null {
  if (reviewResult === null || reviewResult === undefined) return null;
  const match = reviewResult.match(/VERDICT\s*:\s*(APPROVED|CHANGES_REQUESTED)/i);
  if (match) return match[1].toUpperCase() === "APPROVED" ? "Approved" : "Changes requested";
  if (reviewResult.toUpperCase().includes("CHANGES_REQUESTED")) return "Changes requested";
  if (!reviewResult.trim()) return "Changes requested";
  return "Approved";
}
