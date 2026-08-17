"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useTasks } from "@/lib/useTasks";
import Composer from "@/components/Composer";
import WorkRow from "@/components/WorkRow";
import { getWorkView } from "@/lib/workStages";

// The homepage: "ask the team" is the first thing a new user sees, and it
// renders without waiting on any task/project list. See
// docs/wp-web-2-conversation-model.md section A for the before/after
// information architecture this replaces.
function HomeContent() {
  const searchParams = useSearchParams();
  const preselectProject = searchParams.get("project");
  const { tasks, error } = useTasks();

  // The API failure is reported once here, page-wide. The Composer suppresses
  // its own copy on this page so one outage cannot stack duplicate banners,
  // and every section below renders its terminal state (data, empty, or
  // unavailable) instead of being pinned on a loading state by a failed fetch.
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
      <h1 className="small muted" style={{ fontSize: 13, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
        SceneWorks
      </h1>
      <p style={{ fontSize: 18, fontWeight: 600, margin: "2px 0 16px" }}>What should the team work on?</p>

      <Composer defaultProjectId={preselectProject ? Number(preselectProject) : undefined} suppressError />

      {unavailable && (
        <div className="notice error">Cannot reach the SceneWorks API: {error}</div>
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

      <p className="meta" style={{ marginTop: 24 }}>
        Looking for operational counters? See the <Link href="/dashboard">dashboard</Link>.
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
