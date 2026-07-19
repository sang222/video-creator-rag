import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChannelInitWizard } from "@/features/channels/channel-init-wizard";

const apiMocks = vi.hoisted(() => ({
  approveTargetMarketDraft: vi.fn(),
  createCompany: vi.fn(),
  createMinimalMarketChannel: vi.fn(),
  getCompanies: vi.fn(),
  runTargetMarketDraft: vi.fn(),
  updateTargetMarketDraft: vi.fn()
}));

vi.mock("@/lib/api", () => ({
  ...apiMocks,
  queryKeys: { channels: ["channels"], companies: ["companies"] }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ChannelInitWizard /></QueryClientProvider>);
}

describe("ChannelInitWizard market-aware flow", () => {
  beforeEach(() => {
    apiMocks.getCompanies.mockResolvedValue([company]);
    apiMocks.createCompany.mockResolvedValue(company);
    apiMocks.createMinimalMarketChannel.mockResolvedValue({
      channel: { id: draft.channel_id, name: "Small Team AI", key: "small-team-ai" },
      target_market_state: "RESEARCH_DRAFT_REQUIRED",
      profile_activation_allowed: false,
      organic_target_country_supported: false
    });
    apiMocks.runTargetMarketDraft.mockResolvedValue(draft);
    apiMocks.updateTargetMarketDraft.mockResolvedValue({ ...draft, acceptable_secondary_geos: ["CA", "GB"], content_hash: "b".repeat(64) });
    apiMocks.approveTargetMarketDraft.mockResolvedValue({
      decision: "APPROVE",
      profile: { profile_version: 1 },
      profile_activation_allowed: false,
      exact_approved_draft_hash: "b".repeat(64)
    });
  });

  it("shows the five market steps, account-country warning, and no activation action", async () => {
    renderWithQuery();
    expect(screen.getByRole("heading", { name: "Tạo kênh theo thị trường" })).toBeInTheDocument();
    for (const step of ["1. Basics", "2. Target Market", "3. Research Draft", "4. Human Review", "5. Approval"]) {
      expect(screen.getByRole("heading", { name: step })).toBeInTheDocument();
    }
    expect(screen.getByText("Account country does not control organic audience targeting.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /kích hoạt/i })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Công ty *")).toHaveValue(company.id));
  });

  it("accepts minimal input and displays agent confidence and evidence", async () => {
    const user = userEvent.setup();
    renderWithQuery();
    await fillMinimalInput(user);
    await user.click(screen.getByRole("button", { name: "Lưu thông tin tối thiểu" }));
    await waitFor(() => expect(apiMocks.createMinimalMarketChannel).toHaveBeenCalledWith(expect.objectContaining({
      channel_name: "Small Team AI",
      channel_key: "small-team-ai",
      primary_market: "US",
      primary_language: "en",
      primary_locale: "en-US",
      account_country: "VN"
    })));
    expect(apiMocks.createMinimalMarketChannel.mock.calls[0][0]).not.toHaveProperty("currency");
    await user.click(screen.getByRole("button", { name: "Chạy Setup/Research Agent offline" }));
    expect(await screen.findByText("Tin cậy 90%")).toBeInTheDocument();
    expect(screen.getAllByText("Bằng chứng (1)").length).toBeGreaterThan(0);
    expect(screen.getByText("Chỉ đề xuất")).toBeInTheDocument();
  });

  it("lets a human edit and approve the exact hash without auto-activation", async () => {
    const user = userEvent.setup();
    renderWithQuery();
    await fillMinimalInput(user);
    await user.click(screen.getByRole("button", { name: "Lưu thông tin tối thiểu" }));
    await user.click(await screen.findByRole("button", { name: "Chạy Setup/Research Agent offline" }));
    const secondary = await screen.findByLabelText("Thị trường phụ");
    await user.clear(secondary);
    await user.type(secondary, "CA, GB");
    await user.click(screen.getByRole("button", { name: "Lưu bản chỉnh sửa của người vận hành" }));
    await waitFor(() => expect(apiMocks.updateTargetMarketDraft).toHaveBeenCalled());
    const [channelId, payload, expectedHash] = apiMocks.updateTargetMarketDraft.mock.calls[0];
    expect(channelId).toBe(draft.channel_id);
    expect(payload.acceptable_secondary_geos).toEqual(["CA", "GB"]);
    expect(payload).not.toHaveProperty("content_hash");
    expect(expectedHash).toBe(draft.content_hash);

    await user.click(screen.getByRole("button", { name: "Duyệt đúng bản nháp này" }));
    await waitFor(() => expect(apiMocks.approveTargetMarketDraft).toHaveBeenCalledWith(
      draft.channel_id,
      expect.objectContaining({ content_hash: "b".repeat(64) })
    ));
    expect(await screen.findByText(/Hồ sơ sẵn sàng để compile ở phase profile v3/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /kích hoạt/i })).not.toBeInTheDocument();
  });

  it("keeps company bootstrap available when the workspace is empty", async () => {
    apiMocks.getCompanies.mockResolvedValue([]);
    renderWithQuery();
    expect(await screen.findByText("Tạo công ty trước")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo công ty" })).toBeInTheDocument();
  });
});

async function fillMinimalInput(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(screen.getByLabelText("Công ty *")).toHaveValue(company.id));
  await user.type(screen.getByLabelText("Tên kênh *"), "Small Team AI");
  await user.type(screen.getByLabelText("Key kênh *"), "small-team-ai");
  await user.type(screen.getByLabelText("Mục đích kênh *"), "Practical AI workflows for small teams.");
  await user.type(screen.getByLabelText("Tóm tắt khán giả *"), "US small business operators");
  await user.type(screen.getByLabelText("Kênh đích đã biết"), "@SmallTeamAI");
  await user.type(screen.getByLabelText("Quốc gia tài khoản"), "VN");
}

const company = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "VCOS Company",
  slug: "vcos-company",
  description: "",
  status: "active",
  default_currency: "USD"
};

const draft = {
  schema_version: "geo2.target-market-profile-draft.v1",
  draft_id: "22222222-2222-4222-8222-222222222222",
  draft_version: 1,
  channel_id: "33333333-3333-4333-8333-333333333333",
  channel_key: "small-team-ai",
  channel_name: "Small Team AI",
  channel_purpose: "Practical AI workflows",
  target_audience_summary: "US small business operators",
  channel_market_type: "MARKET_NATIVE",
  proposal_authority: "AGENT_PROPOSAL_ONLY",
  status: "NEEDS_HUMAN_REVIEW",
  primary_market: "US",
  primary_geo_cluster: ["US"],
  acceptable_secondary_geos: ["CA", "GB", "AU"],
  primary_locale: "en-US",
  content_language: "en",
  narration_locale: "en-US",
  primary_timezone: "America/New_York",
  spelling_system: "US",
  currency: "USD",
  units_policy: "US_WITH_METRIC_WHEN_RELEVANT",
  date_format: "MMM D, YYYY",
  title_locale: "en-US",
  thumbnail_text_locale: "en-US",
  caption_locales: ["en-US"],
  audience_market_context: "US_SMALL_BUSINESS",
  workplace_context: "US_SMALL_BUSINESS",
  source_jurisdiction_policy: "TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED",
  preferred_source_jurisdictions: ["US"],
  foreign_source_context_required: true,
  allowed_market_contexts: ["US", "CA", "GB", "AU"],
  prohibited_market_mismatches: ["TRANSLATED_SOUNDING_ENGLISH"],
  initial_publish_window_hypotheses: [{ timezone: "America/New_York", local_time: "10:00" }],
  minimum_comparable_videos: 3,
  video_geo_evaluation_window_days: 7,
  channel_geo_review_window_days: 30,
  account_country: "VN",
  target_market: "US",
  actual_viewer_geography_state: "UNMEASURED",
  suggestions: [{
    suggested_field: "currency",
    suggested_value: "USD",
    confidence: 0.9,
    evidence_refs: [{ ref: "offline-fixture://geo2/us" }],
    rationale: "Offline US market policy fixture.",
    missing_information: [],
    human_confirmation_required: true
  }],
  missing_information: [],
  human_confirmation_required: true,
  content_hash: "a".repeat(64)
} as const;
