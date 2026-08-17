"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTasks } from "@/lib/useTasks";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/work", label: "Work" },
  { href: "/projects", label: "Projects" },
  { href: "/company", label: "Team" },
];

const SECONDARY_NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/settings", label: "Settings" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  // Shared with the page-level snapshot: the sidebar never issues its own
  // task-list request, so a mounted page triggers exactly one list poll.
  const { tasks } = useTasks();
  const recent = (tasks ?? []).slice(0, 6);

  useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

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
        <span className="brand-sub">engineering team</span>
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
        <div className="sidebar-section">
          <div className="sidebar-section-title">Recent</div>
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

      <div className="sidebar-section">
        {SECONDARY_NAV.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav secondary${active ? " active" : ""}`}
              aria-busy={pendingHref === item.href}
              onClick={go(item.href, active)}
            >
              {item.label}
            </Link>
          );
        })}
      </div>

      <div className="side-note">
        Managed repositories are never modified in place — agents operate in
        isolated worktrees, and the founder decides what to integrate.
      </div>
    </aside>
  );
}
