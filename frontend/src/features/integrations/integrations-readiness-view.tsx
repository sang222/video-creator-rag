"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, PlayCircle, RefreshCw, ShieldCheck, WalletCards } from "lucide-react";

import { EmptyStateCard, PageHeader, TechnicalAppendix } from "@/components/cockpit";
import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { apiBaseUrl, getIntegrationsReadiness, queryKeys, runIntegrationsReadiness, runProviderSmoke } from "@/lib/api";
import type { IntegrationReadiness, ProviderReadinessCheck, ProviderSummary } from "@/lib/types";

const providerOrder = [
  "ollama",
  "youtube-public",
  "youtube-owner",
  "google-drive",
  "google-vertex-veo",
  "elevenlabs",
  "creatomate",
  "cloud-final-renderer"
];

export function IntegrationsReadinessView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.integrationsReadiness, queryFn: getIntegrationsReadiness });
  const readinessMutation = useMutation({
    mutationFn: runIntegrationsReadiness,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.integrationsReadiness })
  });
  const smokeMutation = useMutation({
    mutationFn: runProviderSmoke,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.integrationsReadiness })
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải cấu hình tích hợp" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải cấu hình tích hợp" /></div>;

  const data = query.data;
  const checksByProvider = groupChecks(data.checks);
  const summaries = providerOrder
    .map((key) => data.provider_summaries.find((summary) => summary.provider_key === key))
    .filter(Boolean) as ProviderSummary[];

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Cấu hình tích hợp"
        subtitle="Kiểm tra trạng thái kết nối, token và nhà cung cấp trước khi chạy môi trường production."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Cài đặt", href: "/settings" }, { label: "Tích hợp" }]}
        primaryAction={
          <Button onClick={() => readinessMutation.mutate()} disabled={readinessMutation.isPending}>
            <RefreshCw size={16} aria-hidden="true" />
            Cập nhật trạng thái
          </Button>
        }
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <FriendlyStatusBadge value={data.snapshot_state} />
            <span className="text-xs text-muted-foreground">Không tự tạo video, upload, publish hoặc reupload.</span>
          </div>
        }
      />

      <section className="grid gap-4 md:grid-cols-3">
        <SummaryTile label="Nhà cung cấp bị chặn" value={data.blocking_items.length} status={data.blocking_items.length ? "BLOCKED" : "PASS"} />
        <SummaryTile label="Cần xem thêm" value={data.warning_items.length} status={data.warning_items.length ? "WARNING" : "PASS"} />
        <SummaryTile label="Lộ secret" value="Không có" status="PASS" />
      </section>

      {summaries.length ? (
        <section className="grid gap-4 xl:grid-cols-2">
          {summaries.map((summary) => (
            <ProviderCard
              key={summary.provider_key}
              summary={summary}
              checks={checksByProvider[summary.provider_key] ?? []}
              onSmoke={() => smokeMutation.mutate(summary.provider_key)}
              smokePending={smokeMutation.isPending}
            />
          ))}
        </section>
      ) : (
        <EmptyStateCard
          title="Chưa có cấu hình tích hợp"
          description="Khi backend có snapshot sẵn sàng, các nhà cung cấp sẽ xuất hiện tại đây với trạng thái và việc tiếp theo rõ ràng."
          actions={[{ label: "Về Trung tâm", href: "/" }]}
        />
      )}

      <BudgetSection data={data} />
    </div>
  );
}

function SummaryTile({ label, value, status }: { label: string; value: React.ReactNode; status: string }) {
  return (
    <Panel className="min-h-28">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm text-muted-foreground">{label}</div>
        <FriendlyStatusBadge value={status} />
      </div>
      <div className="mt-4 text-2xl font-semibold">{value}</div>
    </Panel>
  );
}

function ProviderCard({
  summary,
  checks,
  onSmoke,
  smokePending
}: {
  summary: ProviderSummary;
  checks: ProviderReadinessCheck[];
  onSmoke: () => void;
  smokePending: boolean;
}) {
  const isYouTubeOwner = summary.provider_key === "youtube-owner";
  const isDrive = summary.provider_key === "google-drive";
  const connected = Boolean(summary.safe_config.connected);

  return (
    <Panel className="flex min-h-[320px] flex-col gap-4">
      <PanelHeader>
        <div>
          <PanelTitle>{summary.provider_name}</PanelTitle>
          <p className="mt-1 text-sm text-muted-foreground">{roleCopy(summary)}</p>
        </div>
        <FriendlyStatusBadge value={summary.readiness_state} />
      </PanelHeader>

      <div className="grid gap-3 text-sm md:grid-cols-2">
        {safeFields(summary).map((field) => (
          <div key={field.label} className="rounded-md border border-border/80 bg-muted/25 p-3">
            <div className="text-xs text-muted-foreground">{field.label}</div>
            <div className="mt-1 font-medium">{field.value}</div>
          </div>
        ))}
      </div>

      {summary.missing_env_keys.length ? (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
          <div className="font-medium">Thiếu cấu hình</div>
          <div className="mt-1 text-muted-foreground">Thiếu {summary.missing_env_keys.length} biến môi trường cần thiết. Xem phụ lục kỹ thuật để đối chiếu tên biến.</div>
        </div>
      ) : null}

      <div className="rounded-md border border-border/80 p-3 text-sm">
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">Trạng thái kiểm tra thủ công</span>
          <FriendlyStatusBadge value={summary.smoke_state ?? "UNKNOWN"} />
        </div>
        <p className="mt-2 leading-5">{summary.next_action}</p>
      </div>

      <div className="mt-auto flex flex-wrap gap-2">
        {isYouTubeOwner && !connected ? (
          <Button asChild variant="primary">
            <a href={`${apiBaseUrl}/auth/youtube/start`}>
              <ExternalLink size={16} aria-hidden="true" />
              Kết nối YouTube
            </a>
          </Button>
        ) : null}
        {isDrive && !connected ? (
          <Button asChild variant="primary">
            <a href={`${apiBaseUrl}/auth/google-drive/start`}>
              <ExternalLink size={16} aria-hidden="true" />
              Kết nối Google Drive
            </a>
          </Button>
        ) : null}
        <Button onClick={onSmoke} disabled={smokePending}>
          <PlayCircle size={16} aria-hidden="true" />
          Kiểm tra thủ công
        </Button>
      </div>

      <TechnicalAppendix>
        {summary.missing_env_keys.length ? (
          <div className="rounded-md bg-muted/30 p-3">
            <div className="font-medium text-foreground">Biến môi trường còn thiếu</div>
            <div className="mt-2 break-words">{summary.missing_env_keys.join(", ")}</div>
          </div>
        ) : null}
        <div className="space-y-2">
          {checks.map((check) => (
            <div key={`${check.provider_key}-${check.check_type}-${check.operator_summary}`} className="rounded-md bg-muted/30 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>Loại kiểm tra: {check.check_type}</span>
                <FriendlyStatusBadge value={check.check_state} />
              </div>
              <p className="mt-2 text-muted-foreground">{check.operator_summary}</p>
            </div>
          ))}
        </div>
      </TechnicalAppendix>
    </Panel>
  );
}

function BudgetSection({ data }: { data: IntegrationReadiness }) {
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <WalletCards className="text-primary" size={20} aria-hidden="true" />
        <h2 className="text-xl font-semibold">Ngân sách AI tháng này</h2>
      </div>
      <p className="text-sm text-muted-foreground">Đây là ngân sách cấu hình cứng từ biến môi trường, chưa phải chi phí thực tế đã tiêu.</p>
      {data.budget_cards.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.budget_cards.map((card) => (
            <Panel key={card.key} className="min-h-56">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">{card.provider_name}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{card.role}</p>
                </div>
                <FriendlyStatusBadge value={card.readiness_state} />
              </div>
              <div className="mt-4 grid gap-3 text-sm">
                <BudgetLine label="Gói cấu hình" value={card.configured_plan} />
                <BudgetLine label="Trần tháng đã cấu hình" value={card.configured_monthly_cap} />
                <BudgetLine label="Cơ sở tính ngân sách" value={card.budget_basis} />
              </div>
              {card.missing_env_keys.length ? (
                <p className="mt-4 text-sm text-warning">Còn thiếu {card.missing_env_keys.length} biến môi trường.</p>
              ) : null}
              <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
                <ShieldCheck size={14} aria-hidden="true" />
                Không tính chi phí thực tế trong M12.
              </div>
              {card.missing_env_keys.length ? (
                <div className="mt-4">
                  <TechnicalAppendix>
                    <div className="break-words">{card.missing_env_keys.join(", ")}</div>
                  </TechnicalAppendix>
                </div>
              ) : null}
            </Panel>
          ))}
        </div>
      ) : (
        <EmptyStateCard
          title="Chưa có ngân sách cấu hình"
          description="Thêm biến ngân sách vào môi trường để dashboard hiển thị trần chi phí cứng. Trang này không tự tính chi phí thực tế."
        />
      )}
    </section>
  );
}

function BudgetLine({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border/80 px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value || "Chưa cấu hình"}</span>
    </div>
  );
}

function groupChecks(checks: ProviderReadinessCheck[]) {
  return checks.reduce<Record<string, ProviderReadinessCheck[]>>((acc, check) => {
    acc[check.provider_key] = [...(acc[check.provider_key] ?? []), check];
    return acc;
  }, {});
}

function roleCopy(summary: ProviderSummary) {
  const role: Record<string, string> = {
    ollama: "Router LLM theo lane, không dùng GLM",
    "youtube-public": "Theo dõi thống kê công khai, độ tin cậy học yếu",
    "youtube-owner": "Analytics chủ sở hữu qua OAuth, độ tin cậy học mạnh",
    "google-drive": "Offload media qua quyền drive.file",
    "google-vertex-veo": "Video AI hero, thời lượng 4/6/8 giây, không âm thanh",
    elevenlabs: "Nhà cung cấp giọng đọc theo gói Creator",
    creatomate: "Shorts, card và thumbnail; không ráp video dài",
    "cloud-final-renderer": "Thiếu renderer ráp video dài"
  };
  return role[summary.provider_key] ?? "Nhà cung cấp đã cấu hình";
}

function safeFields(summary: ProviderSummary) {
  const config = summary.safe_config;
  const yesNo = (value: unknown) => (value ? "Có" : "Chưa");
  const fields: Record<string, Array<{ label: string; value: string }>> = {
    ollama: [
      { label: "Base URL", value: String(config.base_url ?? "Chưa cấu hình") },
      { label: "Cờ chạy thật", value: yesNo(config.real_execution_enabled) }
    ],
    "youtube-public": [
      { label: "Khóa API", value: yesNo(config.api_key_configured) },
      { label: "Độ tin cậy học", value: "Yếu" }
    ],
    "youtube-owner": [
      { label: "Kết nối", value: yesNo(config.connected) },
      { label: "Độ tin cậy học", value: "Mạnh" }
    ],
    "google-drive": [
      { label: "Kết nối", value: yesNo(config.connected) },
      { label: "Thư mục gốc", value: yesNo(config.root_folder_configured) }
    ],
    "google-vertex-veo": [
      { label: "Model", value: String(config.model_id ?? "veo-3.1-fast-generate-001") },
      { label: "Quy tắc thời lượng", value: durationRulesLabel(config.duration_rules) }
    ],
    elevenlabs: [
      { label: "Khóa API", value: yesNo(config.api_key_configured) },
      { label: "Cơ sở ngân sách", value: String(config.budget_basis ?? "credits/characters") }
    ],
    creatomate: [
      { label: "Khóa API", value: yesNo(config.api_key_configured) },
      { label: "Vai trò", value: creatomateRoleLabel(config.role) },
      { label: "Renderer video dài", value: config.not_final_long_form_renderer ? "Không dùng" : "Chưa có dữ liệu" }
    ],
    "cloud-final-renderer": [
      { label: "Nhà cung cấp", value: providerNameLabel(config.provider) },
      { label: "Trạng thái", value: providerStateLabel(config.status) },
      { label: "Video dài", value: config.long_form_final_render_blocked ? "Đang bị chặn" : "Chưa có dữ liệu" },
      { label: "Việc tiếp theo", value: String(config.next_action ?? "Chọn renderer ráp video dài sau") }
    ]
  };
  return fields[summary.provider_key] ?? [{ label: "Loại nhà cung cấp", value: "Chưa có dữ liệu" }];
}

function durationRulesLabel(value: unknown) {
  return String(value ?? "4,6,8; tối đa 8 giây").replace("max 8s", "tối đa 8 giây");
}

function creatomateRoleLabel(value: unknown) {
  const raw = String(value ?? "");
  return raw ? raw.replaceAll("/", ", ") : "Shorts, card, thumbnail";
}

function providerNameLabel(value: unknown) {
  const raw = String(value ?? "");
  return {
    creatomate: "Creatomate",
    not_selected: "Chưa chọn"
  }[raw] ?? (raw ? "Nhà cung cấp đã cấu hình" : "Chưa cấu hình");
}

function providerStateLabel(value: unknown) {
  return {
    READY: "Đã sẵn sàng",
    PASS: "Đã sẵn sàng",
    READY_FOR_SMOKE: "Sẵn sàng kiểm tra",
    CONFIGURED: "Đã cấu hình",
    NEEDS_CONFIG: "Cần cấu hình",
    BLOCKED: "Đang bị chặn",
    REQUIRED_GAP: "Thiếu renderer"
  }[String(value ?? "NEEDS_CONFIG").toUpperCase()] ?? "Chưa có dữ liệu";
}
