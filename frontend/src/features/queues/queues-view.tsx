"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getQueues, queryKeys } from "@/lib/api";

const filters = ["all", "learning", "publish", "recovery", "ops"];
const filterLabels: Record<string, string> = {
  all: "Tất cả",
  learning: "Bài học",
  publish: "Gói publish",
  recovery: "Phục hồi",
  ops: "Ops"
};

export function QueuesView({ queueType }: { queueType?: string }) {
  const active = queueType ?? "all";
  const query = useQuery({
    queryKey: queryKeys.queues(active),
    queryFn: () => getQueues(active === "all" ? undefined : active)
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải hàng chờ duyệt" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải hàng chờ duyệt" /></div>;
  const total = query.data.items.length;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Hàng chờ duyệt"
        subtitle="Các việc cần người vận hành xem xét. Chỉ hiển thị action được backend cho phép."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Hàng chờ duyệt" }]}
      />
      <Tabs.Root value={active}>
        <Tabs.List className="flex flex-wrap gap-2">
          {filters.map((filter) => (
            <Tabs.Trigger key={filter} value={filter} className="rounded-md border border-border px-3 py-2 text-sm data-[state=active]:border-primary data-[state=active]:text-primary">
              {filterLabels[filter]}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {query.data.summaries.length === 0 ? (
          <MetricSummaryCard label="Việc đang chờ" value={total} hint="Không có item nghĩa là chưa có việc cần người duyệt trong bộ lọc này." />
        ) : null}
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
        <EmptyStateCard
          title="Không có việc trong hàng chờ"
          description="Hiện chưa có approval, gói publish, đề xuất phục hồi hoặc thao tác ops cần xử lý trong bộ lọc này. Khi backend tạo item mới, nó sẽ xuất hiện tại đây với next action rõ ràng."
          actions={[{ label: "Về Trung tâm điều hành", href: "/" }]}
        />
      )}
    </div>
  );
}
