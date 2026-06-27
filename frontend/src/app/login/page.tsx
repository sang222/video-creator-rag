"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { useCurrentUser } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const auth = useCurrentUser();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await auth.login(email, password);
      router.replace("/");
    } catch {
      setError("Email hoặc mật khẩu không đúng.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <Panel className="w-full max-w-md border-border/80 bg-card/95 p-7 shadow-2xl shadow-black/20">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Lock size={20} aria-hidden="true" />
          </div>
          <div>
            <div className="text-xs uppercase text-muted-foreground">Bảng điều hành VCOS</div>
            <h1 className="text-2xl font-semibold">Đăng nhập để tiếp tục</h1>
          </div>
        </div>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          Chế độ local/dev - chưa phải đăng nhập production.
        </p>
        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm">
            <span className="text-muted-foreground">Email</span>
            <input
              className="mt-2 min-h-11 w-full rounded-md border border-border bg-background px-3 text-foreground outline-none transition focus:border-primary"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
            />
          </label>
          <label className="block text-sm">
            <span className="text-muted-foreground">Mật khẩu</span>
            <input
              className="mt-2 min-h-11 w-full rounded-md border border-border bg-background px-3 text-foreground outline-none transition focus:border-primary"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
            />
          </label>
          {error ? <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-100">{error}</div> : null}
          <Button className="w-full" disabled={loading} type="submit" variant="primary">
            {loading ? "Đang đăng nhập..." : "Đăng nhập"}
          </Button>
        </form>
      </Panel>
    </main>
  );
}
