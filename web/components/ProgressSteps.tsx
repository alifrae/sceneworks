"use client";

import type { Exceptional, ProgressStep } from "@/lib/workStages";

interface ProgressStepsProps {
  steps: ProgressStep[];
  /** When the task is blocked/failed, the first not-yet-done step is shown
   * as stuck there rather than as plain, indistinguishable "pending". */
  exceptional?: Exceptional;
}

export default function ProgressSteps({ steps, exceptional }: ProgressStepsProps) {
  const stuckIndex =
    exceptional === "blocked" || exceptional === "failed" ? steps.findIndex((s) => !s.done) : -1;

  return (
    <ul className="progress-steps">
      {steps.map((step, i) => {
        const isStuck = i === stuckIndex;
        const className = step.done
          ? "done"
          : isStuck
            ? `blocked${exceptional === "failed" ? " failed" : ""}`
            : step.active
              ? "active"
              : "";
        const mark = step.done ? "✓" : isStuck ? (exceptional === "failed" ? "✕" : "!") : step.active ? "●" : "○";
        return (
          <li key={step.key} className={className}>
            <span className="mark">{mark}</span>
            {step.label}
            {isStuck && (
              <span className="muted small">
                — {exceptional === "failed" ? "failed here" : "blocked here"}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
