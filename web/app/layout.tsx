import "./globals.css";
import "./modern.css";
import type { Metadata } from "next";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: {
    default: "SceneWorks",
    template: "%s · SceneWorks",
  },
  description: "AI engineering control plane for governed agentic software work",
};

const themeBootstrap = `
(function () {
  try {
    var stored = window.localStorage.getItem("sceneworks-theme");
    document.documentElement.dataset.theme = stored === "light" ? "light" : "dark";
  } catch (_) {
    document.documentElement.dataset.theme = "dark";
  }
})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body>
        <div className="layout">
          <Sidebar />
          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
