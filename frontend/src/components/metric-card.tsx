import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";

export function MetricCard({
  label,
  value,
  state,
  nextAction
}: {
  label: string;
  value: string | number | null;
  state: string;
  nextAction?: string | null;
}) {
  return (
    <Panel className="min-h-32">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm text-muted-foreground">{label}</div>
        <StatusBadge value={state} />
      </div>
      <div className="mt-4 text-3xl font-semibold">{value ?? "Chưa có dữ liệu"}</div>
      {nextAction ? <div className="mt-3 text-sm text-muted-foreground">{nextAction}</div> : null}
    </Panel>
  );
}
