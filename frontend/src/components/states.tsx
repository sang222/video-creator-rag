import { AlertTriangle, Loader2 } from "lucide-react";

import { Panel } from "@/components/ui/panel";

export function LoadingState({ label = "Loading dashboard data" }: { label?: string }) {
  return (
    <Panel className="flex min-h-40 items-center gap-3">
      <Loader2 className="animate-spin text-primary" size={20} />
      <span className="text-sm text-muted-foreground">{label}</span>
    </Panel>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <Panel className="flex min-h-40 items-center gap-3 border-rose-500/40">
      <AlertTriangle className="text-rose-200" size={20} />
      <span className="text-sm text-rose-100">{message}</span>
    </Panel>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <Panel className="min-h-40">
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="mt-2 text-sm text-muted-foreground">{body}</p>
    </Panel>
  );
}
