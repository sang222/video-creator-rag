"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { Activity, Boxes, CirclePlay, Database, Gauge, GitBranch, Home, ListChecks, LogOut, Settings, Siren, Video } from "lucide-react";

import { FriendlyStatusBadge, friendlyStatusLabel } from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/lib/auth";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/", label: "Trung tâm điều hành", icon: Home },
  { href: "/channels", label: "Không gian kênh", icon: Boxes },
  { href: "/queues", label: "Hàng chờ duyệt", icon: ListChecks },
  { href: "/projects", label: "Dự án", icon: GitBranch },
  { href: "/publishing", label: "Gói publish", icon: CirclePlay },
  { href: "/uploaded-videos", label: "Video đã upload", icon: Video },
  { href: "/learning", label: "Bài học chờ duyệt", icon: Activity },
  { href: "/media", label: "Tệp trên Google Drive", icon: Database },
  { href: "/ops", label: "Trạng thái nhà cung cấp", icon: Gauge },
  { href: "/settings", label: "Hồ sơ & chính sách", icon: Settings }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const auth = useCurrentUser();
  const isLogin = pathname === "/login";

  useEffect(() => {
    if (!isLogin && !auth.isLoading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isAuthenticated, auth.isLoading, isLogin, router]);

  if (isLogin) {
    return <>{children}</>;
  }

  if (auth.isLoading || !auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6 text-sm text-muted-foreground">
        Đang kiểm tra phiên đăng nhập...
      </div>
    );
  }

  const user = auth.session?.user;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 hidden w-72 border-r border-border bg-card/95 px-4 py-5 lg:block">
        <div className="flex items-center gap-3 border-b border-border pb-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Siren size={20} aria-hidden="true" />
          </div>
          <div>
            <div className="text-xs font-semibold uppercase text-accent">VCOS</div>
            <div className="text-lg font-semibold">Bảng điều hành</div>
          </div>
        </div>
        <nav className="mt-6 space-y-1">
          {nav.map((item) => {
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex min-h-10 items-center gap-3 rounded-md px-3 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground",
                  active && "bg-muted text-foreground"
                )}
              >
                <item.icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
      <main className="lg:pl-72">
        <header className="sticky top-0 z-20 border-b border-border bg-background/95 px-4 py-3 backdrop-blur md:px-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase text-muted-foreground">Trung tâm vận hành thủ công</div>
              <div className="text-lg font-semibold">Bảng điều hành VCOS</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <FriendlyStatusBadge value="NO_AUTO_PUBLISH" />
              <span className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">Chỉ CTA Google Drive</span>
              <span className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">
                Vai trò hiện tại: {friendlyStatusLabel(user?.role ?? "READ_ONLY")}
              </span>
              {user?.email ? <span className="text-xs text-muted-foreground">{user.email}</span> : null}
              <Button
                variant="ghost"
                onClick={async () => {
                  await auth.logout();
                  router.replace("/login");
                }}
                aria-label="Đăng xuất"
              >
                <LogOut size={16} aria-hidden="true" />
                Đăng xuất
              </Button>
            </div>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
