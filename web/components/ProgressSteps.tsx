"use client";

import type { ProgressStep } from "@/lib/workStages";

export default function ProgressSteps({ steps }: { steps: ProgressStep[] }) {
  return (
    <ul className="progress-steps">
      {steps.map((step) => (
        <li key={step.key} className={step.done ? "done" : step.active ? "active" : ""}>
          <span className="mark">{step.done ? "✓" : step.active ? "●" : "○"}</span>
          {step.label}
        </li>
      ))}
    </ul>
  );
}
