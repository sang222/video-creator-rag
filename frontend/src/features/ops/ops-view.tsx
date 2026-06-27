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
        title="Vận hành"
        subtitle="Bảng điều hành chỉ đọc trạng thái đã lưu trong backend. Khi mở trang, VCOS không gọi nhà cung cấp thật."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Vận hành" }]}
      />
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {Object.entries(data.integrations).length ? (
          Object.entries(data.integrations).map(([key, value]) => (
            <MetricSummaryCard key={key} label={integrationLabel(key)} value={<StatusBadge value={String(value.state ?? "UNKNOWN")} />} hint="Trạng thái đọc từ DB, không gọi nhà cung cấp." />
          ))
        ) : (
          <MetricSummaryCard label="Tích hợp theo dõi" value="Chưa có dữ liệu" status="UNKNOWN" hint="Khi backend có snapshot vận hành, trạng thái sẽ xuất hiện ở đây." />
        )}
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        {data.providers.length ? (
          <Panel>
            <h2 className="text-base font-semibold">Nhà cung cấp</h2>
            <div className="mt-4 space-y-3">
              {data.providers.map((provider, index) => (
                <div key={index} className="rounded-md border border-border bg-background/35 p-3 text-sm">
                  <div className="font-medium">{providerLabel(String(provider.provider_key ?? provider.provider_type ?? ""))}</div>
                  <div className="mt-2"><StatusBadge value={String(provider.status ?? "UNKNOWN")} /></div>
                </div>
              ))}
            </div>
          </Panel>
        ) : (
          <EmptyStateCard title="Chưa có nhà cung cấp trong dashboard" description="Khi catalog nhà cung cấp được seed, bảng điều hành sẽ hiển thị trạng thái đọc từ backend. Trang này không gọi nhà cung cấp khi mở." />
        )}
        {[...data.incidents, ...data.manual_actions].length ? (
          <Panel>
            <h2 className="text-base font-semibold">Thao tác thủ công / sự cố</h2>
            <div className="mt-4 space-y-3 text-sm text-muted-foreground">
              {[...data.incidents, ...data.manual_actions].map((item, index) => (
                <div key={index} className="rounded-md border border-border bg-background/35 p-3">{opsItemLabel(item)}</div>
              ))}
            </div>
          </Panel>
        ) : (
          <EmptyStateCard title="Không có việc vận hành đang mở" description="Hiện chưa có sự cố hoặc thao tác thủ công cần người vận hành xử lý. Khi backend ghi nhận việc mới, việc tiếp theo sẽ xuất hiện tại đây." />
        )}
      </section>
    </div>
  );
}

function integrationLabel(key: string) {
  return {
    ollama_router: "LLM router",
    google_vertex_veo: "Google Vertex Veo",
    google_drive: "Google Drive",
    youtube_analytics: "Analytics YouTube",
    cloud_final_renderer: "Renderer cuối"
  }[key] ?? "Tích hợp";
}

function providerLabel(key: string) {
  return {
    ollama: "Router LLM",
    "youtube-public": "YouTube công khai",
    "youtube-owner": "YouTube chủ sở hữu",
    "google-drive": "Google Drive",
    "google-vertex-veo": "Google Vertex Veo",
    elevenlabs: "ElevenLabs",
    creatomate: "Creatomate",
    "cloud-final-renderer": "Renderer cuối"
  }[key] ?? "Nhà cung cấp";
}

function opsItemLabel(item: Record<string, unknown>) {
  const nextAction = item.next_action;
  if (typeof nextAction === "string" && nextAction.trim()) return nextAction;
  const rawType = String(item.incident_type ?? item.action_type ?? "");
  return {
    NEEDS_AUTH: "Cần kết nối tài khoản",
    QUOTA_REVIEW: "Cần xem quota",
    COST_REVIEW: "Cần xem chi phí",
    PROVIDER_CHECK: "Cần kiểm tra nhà cung cấp"
  }[rawType] ?? "Việc vận hành cần xem";
}
