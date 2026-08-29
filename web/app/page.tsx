"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import Composer from "@/components/Composer";
import WorkRow from "@/components/WorkRow";
import { useTasks } from "@/lib/useTasks";
import { getWorkView } from "@/lib/workStages";

function HomeContent() {
  const searchParams = useSearchParams();
  const preselectProject = searchParams.get("project");
  const { tasks, error } = useTasks();
  const unavailable = tasks === null && error !== null;

  const needsAttention = (tasks ?? []).filter((task) => getWorkView(task).needsAttention);
  const active = (tasks ?? []).filter((task) => {
    const view = getWorkView(task);
    return !view.needsAttention && view.exceptional === "none" && view.stage !== "completed";
  });
  const recent = (tasks ?? [])
    .filter((task) => ["ACCEPTED", "REJECTED"].includes(task.status))
    .slice(0, 6);

  return (
    <div>
      <section className="home-hero">
        <div className="home-title-row">
          <div>
            <h1>SceneWorks</h1>
            <p className="muted">Engineering work, runtime state and evidence in one place.</p>
          </div>
          <div className="home-links">
            <Link href="/issues">Issues</Link>
            <Link href="/control">Runtime control</Link>
            <Link href="/projects">Projects</Link>
          </div>
        </div>
        <Composer defaultProjectId={preselectProject ? Number(preselectProject) : undefined} suppressError />
      </section>

      {unavailable && (
        <div className="notice error" style={{ marginTop: 16 }}>Cannot reach the SceneWorks API: {error}</div>
      )}

      <section className={`section${tasks !== null && !error && needsAttention.length > 0 ? " attention" : ""}`}>
        <div className="section-head">
          <h3>Needs attention</h3>
          {needsAttention.length > 8 && <Link href="/work?filter=attention" className="small">View all ({needsAttention.length}) →</Link>}
        </div>
        {tasks === null && !error ? (
          <div className="empty">Loading work…</div>
        ) : unavailable ? (
          <div className="empty">Unavailable — API offline.</div>
        ) : needsAttention.length === 0 ? (
          <div className="empty">Nothing needs a decision right now.</div>
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
          <Link href="/work?filter=active" className="small">View all →</Link>
        </div>
        {tasks === null && !error ? (
          <div className="empty">Loading work…</div>
        ) : active.length === 0 ? (
          <div className="empty">No active work.</div>
        ) : (
          <div className="work-list">
            {active.slice(0, 8).map((task) => <WorkRow key={task.id} task={task} />)}
          </div>
        )}
      </section>

      {recent.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h3>Recent results</h3>
            <Link href="/work?filter=completed" className="small">View all →</Link>
          </div>
          <div className="work-list">
            {recent.map((task) => <WorkRow key={task.id} task={task} />)}
          </div>
        </section>
      )}
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
