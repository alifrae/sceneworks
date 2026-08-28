"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import BrandMark from "@/components/BrandMark";
import { useTasks } from "@/lib/useTasks";
import Composer from "@/components/Composer";
import WorkRow from "@/components/WorkRow";
import { getWorkView } from "@/lib/workStages";

function HomeContent() {
  const searchParams = useSearchParams();
  const preselectProject = searchParams.get("project");
  const { tasks, error } = useTasks();

  const unavailable = tasks === null && error !== null;

  const needsAttention = (tasks ?? []).filter((t) => getWorkView(t).needsAttention);
  const active = (tasks ?? []).filter((t) => {
    const v = getWorkView(t);
    return !v.needsAttention && v.exceptional === "none" && v.stage !== "completed";
  });
  const recent = (tasks ?? [])
    .filter((t) => ["ACCEPTED", "REJECTED"].includes(t.status))
    .slice(0, 8);

  function sectionBody(emptyText: string) {
    if (tasks === null && error === null) return <div className="empty">Loading…</div>;
    if (unavailable) return <div className="empty">Unavailable — API offline.</div>;
    return <div className="empty">{emptyText}</div>;
  }

  return (
    <div>
      <section className="home-hero">
        <div className="home-brand-row">
          <BrandMark size={38} />
          <div>
            <div className="hero-brand-name">SceneWorks</div>
            <div className="hero-kicker">AI engineering control plane</div>
          </div>
        </div>

        <h1 className="hero-title">Move engineering work from intent to verified change.</h1>
        <p className="hero-copy">
          Describe the outcome. SceneWorks routes the work, isolates implementation, preserves the evidence,
          and brings decisions back to you before anything is integrated.
        </p>

        <Composer defaultProjectId={preselectProject ? Number(preselectProject) : undefined} suppressError />

        <div className="hero-trust-strip" aria-label="SceneWorks operating guarantees">
          <span className="hero-trust-item">Isolated Git worktrees</span>
          <span className="hero-trust-item">Human approval boundary</span>
          <span className="hero-trust-item">Traceable agent execution</span>
        </div>
      </section>

      {unavailable && (
        <div className="notice error" style={{ marginTop: 16 }}>Cannot reach the SceneWorks API: {error}</div>
      )}

      <section className={`section${tasks !== null && !error && needsAttention.length > 0 ? " attention" : ""}`}>
        <div className="section-head">
          <h3>Needs your attention</h3>
          {needsAttention.length > 8 && (
            <Link href="/work?filter=attention" className="small">
              View all ({needsAttention.length}) →
            </Link>
          )}
        </div>
        {tasks === null ? (
          sectionBody("Nothing needs you right now.")
        ) : needsAttention.length === 0 ? (
          <div className="empty">Nothing needs you right now.</div>
        ) : (
          <div className="work-list">
            {needsAttention.slice(0, 8).map((task) => (
              <WorkRow key={task.id} task={task} reason={getWorkView(task).attentionReason} />
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h3>Active work</h3>
          <Link href="/work?filter=active" className="small">
            View all →
          </Link>
        </div>
        {tasks === null ? (
          sectionBody("No active work. Ask the team something above.")
        ) : active.length === 0 ? (
          <div className="empty">No active work. Ask the team something above.</div>
        ) : (
          <div className="work-list">
            {active.slice(0, 8).map((task) => (
              <WorkRow key={task.id} task={task} />
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h3>Recent results</h3>
          <Link href="/work?filter=completed" className="small">View all →</Link>
        </div>
        {tasks === null ? (
          sectionBody("Nothing completed yet.")
        ) : recent.length === 0 ? (
          <div className="empty">Nothing completed yet.</div>
        ) : (
          <div className="work-list">
            {recent.map((task) => (
              <WorkRow key={task.id} task={task} />
            ))}
          </div>
        )}
      </section>

      <p className="meta" style={{ marginTop: 28 }}>
        Operational counters and provider state live in the <Link href="/dashboard">dashboard</Link>. Performance and connectivity checks live in <Link href="/diagnostics">diagnostics</Link>.
      </p>
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={<h1>SceneWorks</h1>}>
      <HomeContent />
    </Suspense>
  );
}
