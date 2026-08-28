"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import BrandMark from "@/components/BrandMark";
import ThemeToggle from "@/components/ThemeToggle";
import { useTasks } from "@/lib/useTasks";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/work", label: "Work" },
  { href: "/projects", label: "Projects" },
  { href: "/company", label: "Team" },
];

const SECONDARY_NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/diagnostics", label: "Diagnostics" },
  { href: "/settings", label: "Settings" },
];

const PREFETCH_ROUTES = [...NAV, ...SECONDARY_NAV].map((item) => item.href);

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  // Shared with the page-level snapshot: the sidebar never issues its own
  // task-list request, so a mounted page triggers exactly one list poll.
  const { tasks } = useTasks();
  const recent = (tasks ?? []).slice(0, 6);

  useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

  useEffect(() => {
    // Explicitly warm the small set of top-level routes after the shell mounts.
    // Next's automatic prefetch is production-oriented and first navigation in
    // `next dev` can otherwise feel like the menu itself is slow while a route
    // bundle is compiled on demand.
    const warm = () => PREFETCH_ROUTES.forEach((href) => router.prefetch(href));
    if ("requestIdleCallback" in window) {
      const id = window.requestIdleCallback(warm, { timeout: 1200 });
      return () => window.cancelIdleCallback(id);
    }
    const id = window.setTimeout(warm, 250);
    return () => window.clearTimeout(id);
  }, [router]);

  function go(href: string, active: boolean) {
    return (event: React.MouseEvent) => {
      if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !active) {
        setPendingHref(href);
      }
    };
  }

  function warm(href: string) {
    return () => router.prefetch(href);
  }

  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <BrandMark size={34} />
        <div className="brand-wordmark">
          <div className="brand-name">SceneWorks</div>
          <div className="brand-sub">AI engineering control plane</div>
        </div>
      </div>

      {pendingHref && (
        <div className="nav-progress" role="status" aria-live="polite">
          Opening…
        </div>
      )}

      <Link
        href="/"
        prefetch
        className="btn primary new-request"
        onMouseEnter={warm("/")}
        onFocus={warm("/")}
        onClick={go("/", pathname === "/")}
      >
        + New request
      </Link>

      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            prefetch
            className={`nav${active ? " active" : ""}`}
            aria-busy={pendingHref === item.href}
            onMouseEnter={warm(item.href)}
            onFocus={warm(item.href)}
            onClick={go(item.href, active)}
          >
            <span className="nav-dot" aria-hidden="true" />
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
              prefetch
              className={`nav recent${pathname === `/work/${task.id}` ? " active" : ""}`}
              onMouseEnter={warm(`/work/${task.id}`)}
              onFocus={warm(`/work/${task.id}`)}
              onClick={go(`/work/${task.id}`, pathname === `/work/${task.id}`)}
              title={task.title}
            >
              <span className="nav-dot" aria-hidden="true" />
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
              prefetch
              className={`nav secondary${active ? " active" : ""}`}
              aria-busy={pendingHref === item.href}
              onMouseEnter={warm(item.href)}
              onFocus={warm(item.href)}
              onClick={go(item.href, active)}
            >
              <span className="nav-dot" aria-hidden="true" />
              {item.label}
            </Link>
          );
        })}
      </div>

      <div className="sidebar-footer-tools">
        <ThemeToggle />
        <div className="side-note">
          Agents work in isolated worktrees. You decide what gets integrated.
        </div>
      </div>
    </aside>
  );
}
