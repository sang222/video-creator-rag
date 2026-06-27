import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IntegrationsReadinessView } from "@/features/integrations/integrations-readiness-view";

const readinessPayload = {
  generated_at: "2026-06-27T00:00:00Z",
  snapshot_state: "BLOCKED",
  latest_snapshot_id: null,
  provider_summaries: [
    summary("ollama", "Ollama Router", "WARNING", { base_url: "http://localhost:11434", real_execution_enabled: false }),
    summary("youtube-public", "YouTube Public Monitor", "BLOCKED", { api_key_configured: false, learning_authority: "WEAK" }, ["YOUTUBE_DATA_API_KEY"]),
    summary("youtube-owner", "YouTube Owner Analytics", "BLOCKED", { connected: false, learning_authority: "STRONG" }, ["YOUTUBE_OAUTH_CLIENT_SECRETS_FILE_OR_CLIENT_FIELDS"]),
    summary("google-drive", "Google Drive", "BLOCKED", { connected: false, root_folder_configured: false }, ["GOOGLE_DRIVE_ROOT_FOLDER_ID"]),
    summary("google-vertex-veo", "Google Vertex Veo", "WARNING", { model_id: "veo-3.1-fast-generate-001", duration_rules: "4,6,8; max 8s" }),
    summary("elevenlabs", "ElevenLabs", "BLOCKED", { api_key_configured: false, budget_basis: "credits/characters" }, ["ELEVENLABS_API_KEY"]),
    summary("creatomate", "Creatomate", "PASS", { api_key_configured: true, final_renderer_status: "READY_FOR_SMOKE" }),
    summary("cloud-final-renderer", "Cloud Final Renderer", "PASS", { status: "READY_FOR_SMOKE", configuration_state: "CONFIGURED", provider: "Creatomate Growth 10K", plan: "growth_10k", api_key_configured: true })
  ],
  checks: [
    check("cloud-final-renderer", "CAPABILITY", "PASS", "Cloud Final Renderer Creatomate Growth 10K đã ready for smoke."),
    check("youtube-owner", "CREDENTIAL", "BLOCKED", "YouTube owner analytics cần OAuth token")
  ],
  blocking_items: [{ provider_key: "youtube-owner" }],
  warning_items: [{ provider_key: "ollama" }],
  next_actions: [],
  budget_cards: [
    {
      key: "total-ai",
      provider_name: "Tổng budget AI",
      role: "Giới hạn tổng AI hard-env",
      configured_plan: "hard_env",
      configured_monthly_cap: "$250 USD",
      budget_basis: "hard_env",
      readiness_state: "PASS",
      missing_env_keys: [],
      note: "Đây là budget cấu hình cứng từ env, chưa phải chi phí thực tế đã tiêu.",
      technical_appendix: { no_actual_spend_calculation: true }
    },
    {
      key: "google-vertex-veo",
      provider_name: "Google Vertex Veo",
      role: "AI hero video-only",
      configured_plan: "veo-3.1-fast-generate-001",
      configured_monthly_cap: "$75 USD",
      budget_basis: "$0.10 USD / giây 1080p",
      readiness_state: "WARNING",
      missing_env_keys: [],
      note: "Đây là budget cấu hình cứng từ env, chưa phải chi phí thực tế đã tiêu.",
      technical_appendix: {}
    }
  ],
  security_summary: { raw_secret_values_exposed: false },
  technical_appendix: { no_provider_calls_on_get: true }
};

vi.mock("@/lib/api", () => ({
  apiBaseUrl: "http://127.0.0.1:8000",
  getIntegrationsReadiness: vi.fn(async () => readinessPayload),
  runIntegrationsReadiness: vi.fn(async () => ({})),
  runProviderSmoke: vi.fn(async () => ({ run_state: "SKIPPED" })),
  queryKeys: {
    integrationsReadiness: ["integrations-readiness"]
  }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IntegrationsReadinessView />
    </QueryClientProvider>
  );
}

describe("IntegrationsReadinessView", () => {
  it("renders Vietnamese provider readiness, CTAs, budget display, and no secrets", async () => {
    renderWithQuery();

    expect(await screen.findByRole("heading", { name: "Cài đặt" })).toBeInTheDocument();
    expect(screen.getByText("Kiểm tra trạng thái kết nối, token và nhà cung cấp trước khi chạy môi trường production.")).toBeInTheDocument();
    expect(screen.getByText("YouTube Owner Analytics")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kết nối YouTube/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kết nối Google Drive/ })).toBeInTheDocument();
    expect(screen.getByText("Cloud Final Renderer")).toBeInTheDocument();
    expect(screen.getByText("Renderer cuối cho video dài")).toBeInTheDocument();
    expect(screen.getAllByText("Đã cấu hình").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Sẵn sàng kiểm tra").length).toBeGreaterThan(0);
    expect(screen.getByText("Ngân sách AI tháng này")).toBeInTheDocument();
    expect(screen.getAllByText("Đây là ngân sách cấu hình cứng từ biến môi trường, chưa phải chi phí thực tế đã tiêu.").length).toBeGreaterThan(0);
    expect(screen.getByText("$250 USD")).toBeInTheDocument();
    expect(screen.getByText("$75 USD")).toBeInTheDocument();
    expect(screen.getAllByText("Phụ lục kỹ thuật").length).toBeGreaterThan(0);
    expect(screen.queryByText("READY_FOR_SMOKE")).not.toBeInTheDocument();
    expect(screen.queryByText(/sk-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/remaining/i)).not.toBeInTheDocument();
  });
});

function summary(provider_key: string, provider_name: string, readiness_state: string, safe_config: Record<string, unknown>, missing_env_keys: string[] = []) {
  return {
    provider_key,
    provider_name,
    provider_type: "TYPE",
    readiness_state,
    status_label: readiness_state,
    operator_summary: `${provider_name} summary`,
    next_action: "Cấu hình tiếp theo",
    smoke_state: "SKIPPED",
    learning_authority: null,
    safe_config,
    missing_env_keys,
    reason_codes: [],
    technical_appendix: {}
  };
}

function check(provider_key: string, check_type: string, check_state: string, operator_summary: string) {
  return {
    provider_key,
    provider_type: "TYPE",
    check_type,
    check_state,
    operator_summary,
    next_action: "Cấu hình tiếp theo",
    reason_codes: [],
    technical_appendix: {}
  };
}
