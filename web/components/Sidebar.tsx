"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/projects", label: "Projects" },
  { href: "/tasks", label: "Tasks" },
  { href: "/company", label: "Company" },
  { href: "/executions", label: "Executions" },
  { href: "/settings", label: "Settings" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [pendingHref, setPendingHref] = useState<string | null>(null);

  useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

  return (
    <aside className="sidebar">
      {pendingHref && (
        <div className="nav-progress" role="status" aria-live="polite">
          Opening {NAV.find((item) => item.href === pendingHref)?.label ?? "view"}…
        </div>
      )}
      <div className="brand">
        SceneWorks
        <small>company control plane</small>
      </div>
      {NAV.map((item) => {
        const active =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`nav${active ? " active" : ""}`}
            aria-busy={pendingHref === item.href}
            onClick={(event) => {
              if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !active) {
                setPendingHref(item.href);
              }
            }}
          >
            {item.label}
          </Link>
        );
      })}
      <div className="side-note">
        Managed repositories are never modified in place. Agents operate in
        isolated worktrees, and the founder decides what to integrate.
      </div>
    </aside>
  );
}
