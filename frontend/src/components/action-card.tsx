import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import type { DashboardActionCard } from "@/lib/types";

export function ActionCard({ card }: { card: DashboardActionCard }) {
  const body = (
    <Panel className="min-h-40 transition hover:border-primary/60">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted-foreground">{card.title}</div>
          <div className="mt-3 text-4xl font-semibold">{card.count}</div>
        </div>
        <StatusBadge value={card.severity} />
      </div>
      <div className="mt-4 flex items-end justify-between gap-3">
        <p className="text-sm text-muted-foreground">{card.next_action}</p>
        <ArrowRight className="shrink-0 text-primary" size={18} />
      </div>
    </Panel>
  );

  return card.route ? <Link href={card.route}>{body}</Link> : body;
}
