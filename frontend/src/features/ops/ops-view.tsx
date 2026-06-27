"use client";

import { useQuery } from "@tanstack/react-query";

import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getProviderOps, queryKeys } from "@/lib/api";

export function OpsView() {
  const query = useQuery({ queryKey: queryKeys.providerOps, queryFn: getProviderOps });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải trạng thái nhà cung cấp" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải trạng thái nhà cung cấp" /></div>;

  const data = query.data;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Trạng thái nhà cung cấp"
        subtitle="Dashboard chỉ đọc trạng thái đã lưu trong backend. Load trang không gọi provider thật."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Trạng thái nhà cung cấp" }]}
      />
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {Object.entries(data.integrations).map(([key, value]) => (
          <MetricSummaryCard key={key} label={integrationLabel(key)} value={<StatusBadge value={String(value.state ?? "UNKNOWN")} />} hint="Trạng thái đọc từ DB, không gọi provider." />
        ))}
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Nhà cung cấp</h2>
          <div className="mt-4 space-y-3">
            {data.providers.length ? data.providers.map((provider, index) => (
              <div key={index} className="rounded-md border border-border p-3 text-sm">
                <div className="font-medium">{String(provider.provider_key)}</div>
                <div className="mt-2"><StatusBadge value={String(provider.status ?? "UNKNOWN")} /></div>
              </div>
            )) : <EmptyStateCard title="Chưa có provider" description="Chạy config seed để có catalog provider. Bảng điều hành không tự gọi provider khi trang mở." />}
          </div>
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Thao tác thủ công / sự cố</h2>
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            {[...data.incidents, ...data.manual_actions].length ? [...data.incidents, ...data.manual_actions].map((item, index) => (
              <div key={index} className="rounded-md border border-border p-3">{String(item.next_action ?? item.incident_type ?? item.action_type)}</div>
            )) : <EmptyStateCard title="Không có việc ops đang mở" description="Hiện chưa có incident hoặc manual action cần người vận hành xử lý." />}
          </div>
        </Panel>
      </section>
    </div>
  );
}

function integrationLabel(key: string) {
  return {
    ollama_router: "LLM router",
    google_vertex_veo: "Google Vertex Veo",
    google_drive: "Google Drive",
    youtube_analytics: "YouTube analytics",
    cloud_final_renderer: "Renderer cuối"
  }[key] ?? key;
}
