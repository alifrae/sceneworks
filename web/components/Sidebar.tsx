"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/work", label: "Work" },
  { href: "/projects", label: "Projects" },
  { href: "/company", label: "Team" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  const [recent, setRecent] = useState<Task[]>([]);

  useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

  useEffect(() => {
    const load = () => api.tasks({ limit: "6" }).then(setRecent).catch(() => undefined);
    load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, []);

  function go(href: string, active: boolean) {
    return (event: React.MouseEvent) => {
      if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !active) {
        setPendingHref(href);
      }
    };
  }

  return (
    <aside className="sidebar">
      {pendingHref && (
        <div className="nav-progress" role="status" aria-live="polite">
          Opening…
        </div>
      )}
      <div className="brand">
        SceneWorks
        <small>engineering team</small>
      </div>

      <Link href="/" className="btn primary new-request" onClick={go("/", pathname === "/")}>
        + New request
      </Link>

      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`nav${active ? " active" : ""}`}
            aria-busy={pendingHref === item.href}
            onClick={go(item.href, active)}
          >
            {item.label}
          </Link>
        );
      })}

      {recent.length > 0 && (
        <div className="sidebar-recent">
          <div className="sidebar-recent-title">Recent</div>
          {recent.map((task) => (
            <Link
              key={task.id}
              href={`/work/${task.id}`}
              className={`nav recent${pathname === `/work/${task.id}` ? " active" : ""}`}
              onClick={go(`/work/${task.id}`, pathname === `/work/${task.id}`)}
              title={task.title}
            >
              {task.title}
            </Link>
          ))}
        </div>
      )}

      <div className="side-note">
        Managed repositories are never modified in place. Agents operate in
        isolated worktrees, and the founder decides what to integrate.
        <div style={{ marginTop: 8 }}>
          <Link href="/settings">Settings</Link> · <Link href="/dashboard">Dashboard</Link>
        </div>
      </div>
    </aside>
  );
}
