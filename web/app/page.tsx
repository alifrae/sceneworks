"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";
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
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api.tasks({ limit: "50" }).then(setTasks).catch((e) => setError(String(e)));
    load();
    const timer = setInterval(load, 8000);
    return () => clearInterval(timer);
  }, []);

  const needsAttention = (tasks ?? []).filter((t) => getWorkView(t).needsAttention);
  const active = (tasks ?? []).filter((t) => {
    const v = getWorkView(t);
    return !v.needsAttention && v.exceptional === "none" && v.stage !== "completed";
  });
  const recent = (tasks ?? [])
    .filter((t) => ["ACCEPTED", "REJECTED"].includes(t.status))
    .slice(0, 8);

  return (
    <div>
      <h1>SceneWorks</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        What should the team work on?
      </p>

      <Composer defaultProjectId={preselectProject ? Number(preselectProject) : undefined} />

      <section className="panel" style={{ marginTop: 24 }}>
        <div className="row space-between">
          <h3>Needs your attention</h3>
          {needsAttention.length > 8 && (
            <Link href="/work?filter=attention" className="small">
              View all ({needsAttention.length}) →
            </Link>
          )}
        </div>
        {tasks === null && !error ? (
          <div className="empty">Loading…</div>
        ) : error ? (
          <div className="notice error">Cannot reach the SceneWorks API: {error}</div>
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

      <section className="panel">
        <div className="row space-between">
          <h3>Active work</h3>
          <Link href="/work?filter=active" className="small">
            View all →
          </Link>
        </div>
        {tasks === null ? (
          <div className="empty">Loading…</div>
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

      <section className="panel">
        <h3>Recent results</h3>
        {tasks === null ? (
          <div className="empty">Loading…</div>
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

      <p className="small muted" style={{ marginTop: 8 }}>
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
