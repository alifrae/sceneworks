"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

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
  return (
    <aside className="sidebar">
      <div className="brand">
        SceneWorks
        <small>company control plane</small>
      </div>
      {NAV.map((item) => {
        const active =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link key={item.href} href={item.href} className={`nav${active ? " active" : ""}`}>
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
