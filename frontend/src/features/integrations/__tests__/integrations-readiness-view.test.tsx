import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    summary("creatomate", "Creatomate", "PASS", { api_key_configured: true, role: "Shorts/cards/thumbnails", not_final_long_form_renderer: true }),
    summary("cloud-final-renderer", "Cloud Final Renderer", "BLOCKED", { status: "REQUIRED_GAP", configuration_state: "REQUIRED_GAP", provider: "not_selected", long_form_final_render_blocked: true, next_action: "Chọn renderer ráp video dài sau." })
  ],
  checks: [
    check("google-drive", "CREDENTIAL", "BLOCKED", "Google Drive cần OAuth client/token.", { oauth_client_configured: false, token_connected: false }),
    check("cloud-final-renderer", "CAPABILITY", "BLOCKED", "Long-form final render vẫn bị chặn cho đến khi chọn và cấu hình renderer ráp video dài."),
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

let readinessResponse: unknown = readinessPayload;

vi.mock("@/lib/api", () => ({
  apiBaseUrl: "http://127.0.0.1:8000",
  getIntegrationsReadiness: vi.fn(async () => readinessResponse),
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
  beforeEach(() => {
    readinessResponse = readinessPayload;
  });

  it("renders Vietnamese provider readiness, CTAs, budget display, and no secrets", async () => {
    renderWithQuery();

    expect(await screen.findByRole("heading", { name: "Cấu hình tích hợp" })).toBeInTheDocument();
    expect(screen.getByText("Kiểm tra trạng thái kết nối, token và nhà cung cấp trước khi chạy môi trường production.")).toBeInTheDocument();
    expect(screen.getByText("YouTube Owner Analytics")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kết nối YouTube/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cần cấu hình OAuth/ })).toBeDisabled();
    expect(screen.queryByRole("link", { name: /Kết nối Google Drive/ })).not.toBeInTheDocument();
    expect(screen.getByText("Google Drive chưa thể mở luồng cấp quyền. Hãy cấu hình client, secret, redirect URI và scope drive.file trước.")).toBeInTheDocument();
    expect(screen.getByText("Cloud Final Renderer")).toBeInTheDocument();
    expect(screen.getByText("Thiếu renderer ráp video dài")).toBeInTheDocument();
    expect(screen.getByText("Chưa chọn")).toBeInTheDocument();
    expect(screen.getAllByText("Loại kiểm tra: Thông tin kết nối").length).toBeGreaterThan(0);
    expect(screen.getByText("Loại kiểm tra: Năng lực")).toBeInTheDocument();
    expect(screen.getAllByText("Đang bị chặn").length).toBeGreaterThan(0);
    expect(screen.getByText("Không dùng")).toBeInTheDocument();
    expect(screen.getByText("Ngân sách AI tháng này")).toBeInTheDocument();
    expect(screen.getAllByText("Đây là ngân sách cấu hình cứng từ biến môi trường, chưa phải chi phí thực tế đã tiêu.").length).toBeGreaterThan(0);
    expect(screen.getByText("$250 USD")).toBeInTheDocument();
    expect(screen.getByText("$75 USD")).toBeInTheDocument();
    expect(screen.getAllByText("Phụ lục kỹ thuật").length).toBeGreaterThan(0);
    expect(screen.queryByText("READY_FOR_SMOKE")).not.toBeInTheDocument();
    expect(screen.queryByText(/sk-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/remaining/i)).not.toBeInTheDocument();
  });

  it("keeps Google Drive ready when only the manual real smoke failed", async () => {
    readinessResponse = {
      ...readinessPayload,
      snapshot_state: "BLOCKED",
      provider_summaries: [
        {
          ...summary("google-drive", "Google Drive", "BLOCKED", { connected: true, root_folder_configured: true }),
          smoke_state: "FAILED"
        }
      ],
      checks: [
        check("google-drive", "CONFIG", "PASS", "Google Drive offload và root folder đã cấu hình."),
        check("google-drive", "CREDENTIAL", "PASS", "Google Drive OAuth token đã kết nối.", { oauth_client_configured: true, token_connected: true }),
        check("google-drive", "SECURITY", "PASS", "Google Drive chỉ dùng scope drive.file."),
        check("google-drive", "REAL_SMOKE", "FAILED", "Drive real smoke failed in a guarded test folder.")
      ],
      blocking_items: [{ provider_key: "google-drive", check_type: "REAL_SMOKE" }],
      warning_items: [],
      budget_cards: []
    };

    renderWithQuery();

    expect(await screen.findByRole("heading", { name: "Cấu hình tích hợp" })).toBeInTheDocument();
    const blockedTile = screen.getByText("Nhà cung cấp bị chặn").closest("section");
    expect(blockedTile).not.toBeNull();
    expect(within(blockedTile as HTMLElement).getByText("0")).toBeInTheDocument();
    expect(screen.getAllByText("Thất bại").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Đã sẵn sàng").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /Kết nối Google Drive/ })).not.toBeInTheDocument();
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

function check(provider_key: string, check_type: string, check_state: string, operator_summary: string, technical_appendix: Record<string, unknown> = {}) {
  return {
    provider_key,
    provider_type: "TYPE",
    check_type,
    check_state,
    operator_summary,
    next_action: "Cấu hình tiếp theo",
    reason_codes: [],
    technical_appendix
  };
}
