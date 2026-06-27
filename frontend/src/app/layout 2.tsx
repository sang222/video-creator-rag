import type { Metadata } from "next";
import Link from "next/link";
import { Activity, Boxes, CirclePlay, Database, Gauge, GitBranch, Home, ListChecks, Settings, Siren, Video } from "lucide-react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "VCOS Operator Dashboard",
  description: "Action-first cockpit for VCOS operators"
};

const nav = [
  { href: "/", label: "Command", icon: Home },
  { href: "/channels", label: "Channels", icon: Boxes },
  { href: "/queues", label: "Approvals", icon: ListChecks },
  { href: "/projects", label: "Projects", icon: GitBranch },
  { href: "/publishing", label: "Publishing", icon: CirclePlay },
  { href: "/uploaded-videos", label: "Uploaded", icon: Video },
  { href: "/learning", label: "Learning", icon: Activity },
  { href: "/media", label: "Drive Media", icon: Database },
  { href: "/ops", label: "Ops", icon: Gauge },
  { href: "/settings", label: "Settings", icon: Settings }
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <Providers>
          <div className="min-h-screen bg-background text-foreground">
            <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-border bg-card/95 px-4 py-5 lg:block">
              <div className="flex items-center gap-3 border-b border-border pb-5">
                <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <Siren size={20} />
                </div>
                <div>
                  <div className="text-sm font-semibold uppercase tracking-[0.18em] text-accent">VCOS</div>
                  <div className="text-lg font-semibold">Signal Deck</div>
                </div>
              </div>
              <nav className="mt-6 space-y-1">
                {nav.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex min-h-10 items-center gap-3 rounded-md px-3 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
                  >
                    <item.icon size={17} aria-hidden="true" />
                    <span>{item.label}</span>
                  </Link>
                ))}
              </nav>
            </aside>
            <main className="lg:pl-64">
              <header className="sticky top-0 z-20 border-b border-border bg-background/95 px-4 py-3 backdrop-blur md:px-8">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase text-muted-foreground">Human-operated control center</div>
                    <div className="text-lg font-semibold">Operator Dashboard</div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded-md border border-border px-2 py-1">No auto publish</span>
                    <span className="rounded-md border border-border px-2 py-1">Drive CTA only</span>
                  </div>
                </div>
              </header>
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
