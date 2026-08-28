// Central state-mapping module: backend TaskStatus -> user-facing "Work"
// concepts. This is the ONLY place that translates raw workflow state into
// what the UI shows. See docs/wp-web-2-conversation-model.md section C for
// the explicit backend-state -> stage -> action table this module encodes.

import type { Task } from "./types";

export type PrimaryStage =
  | "submitted"
  | "investigating"
  | "architecture"
  | "implementing"
  | "reviewing"
  | "completed";

export type Exceptional = "none" | "needs_input" | "blocked" | "failed" | "cancelled";

export type Outcome = "accepted" | "rejected" | null;

export const STAGE_LABELS: Record<PrimaryStage, string> = {
  submitted: "Submitted",
  investigating: "Investigating",
  architecture: "Architecture",
  implementing: "Implementing",
  reviewing: "Reviewing",
  completed: "Completed",
};

export const EXCEPTIONAL_LABELS: Record<Exclude<Exceptional, "none">, string> = {
  needs_input: "Needs your input",
  blocked: "Blocked",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const ROLE_LABELS: Record<string, string> = {
  triage: "Triage",
  product: "Product",
  cto: "CTO",
  technical_expert: "Technical Expert",
  architect: "Architect",
  engineer: "Engineer",
  reviewer: "Reviewer",
};

export interface ProgressStep {
  key: string;
  label: string;
  done: boolean;
  active: boolean;
  skipped?: boolean;
}

export interface WorkView {
  stage: PrimaryStage;
  displayLabel: string;
  exceptional: Exceptional;
  attentionReason: string | null;
  needsAttention: boolean;
  ownerRole: string | null;
  ownerLabel: string;
  isAdvisoryOnly: boolean;
  outcome: Outcome;
  progress: ProgressStep[];
}

const RUNNING_EXEC_STATUSES = new Set(["QUEUED", "STARTING", "RUNNING"]);
const UNDERSTANDING_ROLES = new Set(["triage", "product", "cto", "technical_expert"]);
const FINISHED_STATUSES = new Set(["READY_FOR_HUMAN", "ACCEPTED", "REJECTED"]);

function isAdvisory(task: Task): boolean {
  return FINISHED_STATUSES.has(task.status) && !task.implementation_summary && !task.review_result;
}

export function getWorkView(task: Task): WorkView {
  const status = task.status;
  const advisoryOnly = isAdvisory(task);
  const hasActiveExecution = !!task.current_execution_id && RUNNING_EXEC_STATUSES.has(task.execution_status || "");

  let stage: PrimaryStage = "submitted";
  let exceptional: Exceptional = "none";
  let attentionReason: string | null = null;
  let ownerRole: string | null = task.current_role;
  let outcome: Outcome = null;

  switch (status) {
    case "NEW":
      stage = "submitted";
      ownerRole = null;
      break;
    case "ARCHITECTURE_ANALYSIS":
      stage = task.current_role && !UNDERSTANDING_ROLES.has(task.current_role) ? "architecture" : "investigating";
      break;
    case "AWAITING_ARCHITECTURE_APPROVAL":
      stage = "architecture";
      exceptional = "needs_input";
      attentionReason = "Architecture plan is waiting for your approval.";
      ownerRole = null;
      break;
    case "READY_TO_IMPLEMENT":
      stage = "implementing";
      ownerRole = null;
      break;
    case "IMPLEMENTING":
    case "TESTING":
      stage = "implementing";
      ownerRole = "engineer";
      break;
    case "REVIEWING":
      stage = "reviewing";
      ownerRole = "reviewer";
      break;
    case "CHANGES_REQUESTED":
      stage = "implementing";
      if (hasActiveExecution) {
        ownerRole = "engineer";
      } else {
        exceptional = "blocked";
        attentionReason = "The reviewer requested changes and the automatic repair limit was reached. Decide how to proceed.";
        ownerRole = null;
      }
      break;
    case "READY_FOR_HUMAN":
      stage = "completed";
      exceptional = "needs_input";
      attentionReason = advisoryOnly
        ? "Findings are ready for you to review."
        : "The reviewer approved the work — accept it or send it back.";
      ownerRole = null;
      break;
    case "ACCEPTED":
      stage = "completed";
      outcome = "accepted";
      ownerRole = null;
      break;
    case "REJECTED":
      stage = "completed";
      outcome = "rejected";
      ownerRole = null;
      break;
    case "FAILED":
      exceptional = "failed";
      attentionReason = "Execution failed and needs a decision (retry or stop here).";
      ownerRole = null;
      break;
    case "CANCELLED":
      exceptional = "cancelled";
      ownerRole = null;
      break;
    default:
      stage = "submitted";
  }

  const displayLabel = status === "NEW"
    ? "Backlog"
    : exceptional !== "none"
      ? EXCEPTIONAL_LABELS[exceptional]
      : STAGE_LABELS[stage];

  const ownerLabel = ownerRole
    ? ROLE_LABELS[ownerRole] || ownerRole
    : exceptional === "needs_input"
      ? "You"
      : status === "NEW"
        ? "Backlog"
        : stage === "completed"
          ? "—"
          : "—";

  return {
    stage,
    displayLabel,
    exceptional,
    attentionReason,
    needsAttention: exceptional === "needs_input" || exceptional === "blocked" || exceptional === "failed",
    ownerRole,
    ownerLabel,
    isAdvisoryOnly: advisoryOnly,
    outcome,
    progress: buildProgress(task, stage, exceptional, advisoryOnly),
  };
}

function buildProgress(
  task: Task,
  stage: PrimaryStage,
  exceptional: Exceptional,
  advisoryOnly: boolean,
): ProgressStep[] {
  const understood = task.status !== "NEW";
  const investigationDone = understood && (task.current_role === "architect" || stage !== "investigating");
  const architectureDone = !!task.architecture_result;
  const approved = ["READY_TO_IMPLEMENT", "IMPLEMENTING", "TESTING", "REVIEWING", "CHANGES_REQUESTED", "READY_FOR_HUMAN", "ACCEPTED"].includes(task.status) || (advisoryOnly && task.status === "READY_FOR_HUMAN");
  const implementingDone = !!task.implementation_summary;
  const reviewDone = !!task.review_result && task.review_result.toUpperCase().includes("APPROVED");
  const complete = task.status === "ACCEPTED" || task.status === "REJECTED";
  const frozen = exceptional === "failed" || exceptional === "cancelled" || exceptional === "blocked";

  const steps: ProgressStep[] = [
    { key: "understood", label: "Request understood", done: understood, active: !frozen && !understood },
    { key: "investigation", label: "Investigation complete", done: investigationDone, active: !frozen && understood && !investigationDone },
  ];

  if (advisoryOnly) {
    steps.push({
      key: "findings",
      label: "Findings ready",
      done: !!task.architecture_result && (task.status === "READY_FOR_HUMAN" || complete),
      active: !frozen && investigationDone && !(task.status === "READY_FOR_HUMAN" || complete),
    });
  } else {
    steps.push(
      { key: "architecture", label: "Architecture proposed", done: architectureDone, active: !frozen && investigationDone && !architectureDone },
      { key: "approved", label: "Approved", done: approved, active: !frozen && architectureDone && !approved },
      { key: "implementing", label: "Implementing", done: implementingDone, active: !frozen && approved && !implementingDone },
      { key: "review", label: "Reviewed", done: reviewDone, active: !frozen && implementingDone && !reviewDone },
    );
  }

  steps.push({ key: "complete", label: "Complete", done: complete, active: false });
  return steps;
}

/** Human-triggerable actions. Internal state-machine transitions stay hidden. */
export const ACTION_INTENT: Record<string, { label: string; kind: "primary" | "danger" | "neutral"; needsNote?: string }> = {
  start_architecture: { label: "Start work", kind: "primary" },
  approve_architecture: { label: "Approve plan", kind: "primary" },
  reject_architecture: { label: "Reject plan", kind: "danger", needsNote: "Reason" },
  request_architecture_revision: { label: "Request changes", kind: "neutral", needsNote: "What should change?" },
  accept: { label: "Accept result", kind: "primary" },
  reject: { label: "Reject result", kind: "danger", needsNote: "Reason" },
  send_back_to_engineer: { label: "Send back to Engineer", kind: "neutral", needsNote: "What needs fixing?" },
  cancel: { label: "Cancel", kind: "danger" },
  retry: { label: "Retry", kind: "primary" },
  retry_architecture: { label: "Retry from architecture", kind: "primary" },
  start_implementation: { label: "Resume implementation", kind: "primary" },
};

export function meaningfulActions(actions: string[]): string[] {
  return actions.filter((a) => a in ACTION_INTENT);
}
