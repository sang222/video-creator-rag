"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getQueues, queryKeys } from "@/lib/api";

const filters = ["all", "learning", "publish", "recovery", "ops"];

export function QueuesView({ queueType }: { queueType?: string }) {
  const active = queueType ?? "all";
  const query = useQuery({
    queryKey: queryKeys.queues(active),
    queryFn: () => getQueues(active === "all" ? undefined : active)
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading approval queues" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading approval queues" /></div>;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Unified Approval Center</h1>
        <p className="mt-1 text-sm text-muted-foreground">Evidence-first queue items with allowed actions only.</p>
      </div>
      <Tabs.Root value={active}>
        <Tabs.List className="flex flex-wrap gap-2">
          {filters.map((filter) => (
            <Tabs.Trigger key={filter} value={filter} className="rounded-md border border-border px-3 py-2 text-sm data-[state=active]:border-primary data-[state=active]:text-primary">
              {filter}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {query.data.summaries.map((summary) => (
          <Panel key={summary.queue_type}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm text-muted-foreground">{summary.label}</div>
                <div className="mt-2 text-3xl font-semibold">{summary.count}</div>
              </div>
              <StatusBadge value={summary.priority} />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{summary.next_action}</p>
          </Panel>
        ))}
      </div>
      {query.data.items.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {query.data.items.map((item) => (
            <ApprovalCard key={`${item.queue_type}-${item.queue_item_id ?? item.entity_id}`} item={item} />
          ))}
        </div>
      ) : (
        <EmptyState title="No queue items" body="There are no operator actions in this queue right now." />
      )}
    </div>
  );
}
