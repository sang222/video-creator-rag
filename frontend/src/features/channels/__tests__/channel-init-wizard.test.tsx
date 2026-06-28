import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChannelInitWizard } from "@/features/channels/channel-init-wizard";

const apiMocks = vi.hoisted(() => ({
  activateChannel: vi.fn(),
  compileChannelInitDraft: vi.fn(),
  createChannelInitDraft: vi.fn(),
  createCompany: vi.fn(),
  getCompanies: vi.fn(),
  initChannel: vi.fn(),
  researchChannelInitDraft: vi.fn(),
  reviewChannelInitDraft: vi.fn()
}));

vi.mock("@/lib/api", () => ({
  activateChannel: apiMocks.activateChannel,
  compileChannelInitDraft: apiMocks.compileChannelInitDraft,
  createChannelInitDraft: apiMocks.createChannelInitDraft,
  createCompany: apiMocks.createCompany,
  getCompanies: apiMocks.getCompanies,
  initChannel: apiMocks.initChannel,
  researchChannelInitDraft: apiMocks.researchChannelInitDraft,
  reviewChannelInitDraft: apiMocks.reviewChannelInitDraft,
  queryKeys: {
    channels: ["channels"],
    companies: ["companies"]
  }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChannelInitWizard />
    </QueryClientProvider>
  );
}

describe("ChannelInitWizard", () => {
  beforeEach(() => {
    apiMocks.activateChannel.mockResolvedValue({ contract_status: "COMPLETE" });
    apiMocks.compileChannelInitDraft.mockResolvedValue(compileResult);
    apiMocks.createChannelInitDraft.mockResolvedValue(initDraft);
    apiMocks.createCompany.mockResolvedValue(company);
    apiMocks.getCompanies.mockResolvedValue([company]);
    apiMocks.initChannel.mockResolvedValue({ channel: { id: "channel-1" }, compiled: { id: "snapshot-1", contract_status: "COMPLETE" } });
    apiMocks.researchChannelInitDraft.mockResolvedValue(contractDraft);
    apiMocks.reviewChannelInitDraft.mockResolvedValue(reviewedContractDraft);
  });

  it("renders minimal research-assisted wizard by default without template preset", async () => {
    renderWithQuery();

    expect(screen.getByRole("heading", { name: "Tạo kênh" })).toBeInTheDocument();
    expect(screen.getByText("1. Thiết lập tối thiểu")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo nháp & research hồ sơ kênh" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nâng cao: nhập thủ công toàn bộ hồ sơ" })).toBeInTheDocument();
    expect(screen.getByText("Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime.")).toBeInTheDocument();
    expect(screen.getByText("Không dùng YouTube Studio scraping.")).toBeInTheDocument();
    expect(screen.getByText("Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.")).toBeInTheDocument();
    expect(screen.queryByText("Template hồ sơ *")).not.toBeInTheDocument();
    expect(screen.queryByText("Thông tin kênh")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Công ty *")).toHaveValue(company.id));
  });

  it("keeps advanced manual mode available", async () => {
    const user = userEvent.setup();
    renderWithQuery();

    await user.click(screen.getByRole("button", { name: "Nâng cao: nhập thủ công toàn bộ hồ sơ" }));

    expect(screen.getByText("Thông tin kênh")).toBeInTheDocument();
    expect(screen.getByText("Template hồ sơ *")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo và compile snapshot" })).toBeInTheDocument();
  });

  it("shows create-company CTA when no companies exist", async () => {
    apiMocks.getCompanies.mockResolvedValue([]);

    renderWithQuery();

    expect(await screen.findByText("Tạo công ty trước")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo công ty" })).toBeInTheDocument();
    expect(screen.queryByLabelText("ID công ty *")).not.toBeInTheDocument();
  });

  it("submits minimal draft and research without template key", async () => {
    const user = userEvent.setup();
    renderWithQuery();

    await fillMinimalFields(user);
    await user.click(screen.getByRole("button", { name: "Tạo nháp & research hồ sơ kênh" }));

    await waitFor(() => expect(apiMocks.createChannelInitDraft).toHaveBeenCalled());
    expect(apiMocks.createChannelInitDraft.mock.calls[0][0]).toMatchObject({
      company_id: company.id,
      channel_name: "Small Team AI",
      public_presence_mode: "EXISTING_PUBLIC_CHANNEL",
      youtube_url_or_handle: "https://www.youtube.com/@SmallTeamAI",
      website_url: "https://smallteamai.com/",
      owner_operator_language: "vi-VN",
      source_usage_attestation: true
    });
    expect(apiMocks.createChannelInitDraft.mock.calls[0][0]).not.toHaveProperty("template_key");
    expect(apiMocks.researchChannelInitDraft).toHaveBeenCalledWith(initDraft.id);
    expect(await screen.findByText("2. Kết quả research")).toBeInTheDocument();
    expect(screen.getByText("Evidence refs")).toBeInTheDocument();
    expect(screen.getAllByText(/Confidence:/).length).toBeGreaterThan(0);
    expect(screen.getByText("Provider policy")).toBeInTheDocument();
    expect(screen.getByText("Safety policy")).toBeInTheDocument();
  });

  it("requires human confirmation before COMPLETE activation CTA", async () => {
    const user = userEvent.setup();
    renderWithQuery();

    await fillMinimalFields(user);
    await user.click(screen.getByRole("button", { name: "Tạo nháp & research hồ sơ kênh" }));
    await screen.findByText("3. Người vận hành rà soát");
    expect(screen.queryByRole("button", { name: "Kích hoạt kênh" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Xác nhận các field bắt buộc" }));
    await waitFor(() => expect(apiMocks.reviewChannelInitDraft).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Compile Channel Contract" }));
    await waitFor(() => expect(apiMocks.compileChannelInitDraft).toHaveBeenCalledWith(initDraft.id));

    expect(await screen.findByRole("button", { name: "Kích hoạt kênh" })).toBeInTheDocument();
  });
});

async function fillMinimalFields(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getByLabelText("Công ty *")).toHaveValue(company.id);
  });
  await user.type(screen.getByLabelText("Tên kênh *"), "Small Team AI");
  await user.type(screen.getByLabelText("YouTube URL/handle"), "https://www.youtube.com/@SmallTeamAI");
  await user.type(screen.getByLabelText("Website URL"), "https://smallteamai.com/");
  await user.type(screen.getByLabelText("Ghi chú ngắn của operator *"), "Kênh chia sẻ AI workflows thực tế, automation systems, dashboards cho đội ngũ nhỏ.");
  await user.type(screen.getByLabelText("Initial topic/pillar hints"), "AI workflows\nautomation systems\noperating dashboards");
  await user.click(screen.getByLabelText("Tôi xác nhận các URL này là nguồn công khai hoặc được phép dùng để research hồ sơ kênh."));
}

const company = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "VCOS Company",
  slug: "vcos-company",
  description: "",
  status: "active",
  default_currency: "USD"
};

const initDraft = {
  id: "22222222-2222-4222-8222-222222222222",
  company_id: company.id,
  channel_name: "Small Team AI",
  public_presence_mode: "EXISTING_PUBLIC_CHANNEL",
  youtube_url_or_handle: "https://www.youtube.com/@SmallTeamAI",
  website_url: "https://smallteamai.com/",
  social_profile_links: [],
  operator_note_purpose: "Kênh chia sẻ AI workflows thực tế.",
  intended_content_language: null,
  intended_primary_market: null,
  owner_operator_language: "vi-VN",
  initial_topic_pillar_hints: ["AI workflows", "automation systems", "operating dashboards"],
  source_usage_attestation: true,
  workflow_status: "NEEDS_HUMAN_REVIEW",
  contract_status: "PARTIAL",
  channel_id: null,
  channel_profile_version_id: null,
  compiled_policy_snapshot_id: null,
  latest_contract_draft: null,
  created_at: "2026-06-28T00:00:00Z",
  updated_at: "2026-06-28T00:00:00Z"
};

const fieldSourceMap = {
  "market_locale.primary_market": meta("UNKNOWN", "UNKNOWN", true),
  "market_locale.audience_locale": meta("en-US", "RESEARCH_INFERENCE", true),
  "market_locale.content_language": meta("en", "RESEARCH_INFERENCE", true),
  "target_audience.primary_persona": meta("Small business owners and team leads", "RESEARCH_INFERENCE", true),
  "channel_identity.niche": meta("Practical AI workflows / automation systems / operating dashboards", "ADMIN_HINT", true),
  "channel_identity.positioning": meta("Clear implementation-first guidance", "ADMIN_HINT", true),
  "editorial_strategy.content_pillars": meta(["AI workflows", "automation systems", "operating dashboards"], "ADMIN_HINT", true),
  "editorial_strategy.claim_style": meta(["practical", "evidence_bounded"], "RESEARCH_INFERENCE", true),
  "format_policy.long_form.enabled": meta(true, "RESEARCH_INFERENCE", true),
  "format_policy.shorts.enabled": meta(true, "RESEARCH_INFERENCE", true),
  "rights_policy.source_manifest_required": meta(true, "GLOBAL_LOCKED_POLICY", false),
  "learning_policy.min_evidence_required": meta("2 source refs", "RESEARCH_INFERENCE", true),
  "media_policy.voice_provider": meta("ElevenLabs", "PROVIDER_POLICY", false),
  "media_policy.ai_hero_provider": meta("Google Vertex Veo", "PROVIDER_POLICY", false),
  "media_policy.renderer": meta("Creatomate Growth 10K", "PROVIDER_POLICY", false),
  "platform_strategy.auto_publish_allowed": meta(false, "GLOBAL_LOCKED_POLICY", false),
  "platform_strategy.studio_scraping_allowed": meta(false, "GLOBAL_LOCKED_POLICY", false),
  "learning_policy.config_mutation_by_agent_allowed": meta(false, "GLOBAL_LOCKED_POLICY", false),
  "platform_strategy.publish_mode": meta("human_handoff_only", "GLOBAL_LOCKED_POLICY", false),
  forbidden_behavior: meta(["fake_traffic", "bot_engagement", "platform_evasion"], "GLOBAL_LOCKED_POLICY", false)
};

const suggestedContract = {
  channel_identity: { niche: "Practical AI workflows / automation systems / operating dashboards", positioning: "Clear implementation-first guidance" },
  target_audience: { primary_persona: "Small business owners and team leads" },
  market_locale: { primary_market: "UNKNOWN", audience_locale: "en-US", content_language: "en" },
  editorial_strategy: { content_pillars: ["AI workflows", "automation systems", "operating dashboards"], claim_style: ["practical", "evidence_bounded"] },
  format_policy: { long_form: { enabled: true }, shorts: { enabled: true } },
  rights_policy: { source_manifest_required: true },
  learning_policy: { min_evidence_required: "2 source refs", config_mutation_by_agent_allowed: false },
  media_policy: { voice_provider: "ElevenLabs", ai_hero_provider: "Google Vertex Veo", renderer: "Creatomate Growth 10K" },
  platform_strategy: { auto_publish_allowed: false, studio_scraping_allowed: false, publish_mode: "human_handoff_only" },
  forbidden_behavior: ["fake_traffic", "bot_engagement", "platform_evasion"]
};

const contractDraft = {
  id: "33333333-3333-4333-8333-333333333333",
  init_draft_id: initDraft.id,
  company_id: company.id,
  channel_name: "Small Team AI",
  source_urls: [],
  admin_minimal_input: {},
  suggested_channel_contract: suggestedContract,
  field_source_map_json: fieldSourceMap,
  confidence_summary: {},
  missing_fields: ["market_locale.primary_market:requires_human_confirmation"],
  human_questions: [],
  risks: [{ risk_code: "RESEARCH_DRAFT_NOT_RUNTIME_TRUTH", message_vi: "Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime." }],
  evidence_refs: [{ ref_id: "ev_website_public_anchor", source_type: "PUBLIC_WEB", url: "https://smallteamai.com/", title: "Website", snippet: "Anchor", captured_at: "2026-06-28T00:00:00Z", reliability: "MEDIUM" }],
  workflow_status: "NEEDS_HUMAN_REVIEW",
  contract_status: "PARTIAL",
  review_decision_log_json: [],
  created_at: "2026-06-28T00:00:00Z",
  updated_at: "2026-06-28T00:00:00Z"
};

const reviewedContractDraft = {
  ...contractDraft,
  workflow_status: "READY_TO_COMPILE",
  contract_status: "COMPLETE",
  missing_fields: []
};

const compileResult = {
  init_draft_id: initDraft.id,
  channel_id: "channel-1",
  channel_profile_version_id: "profile-1",
  compiled_policy_snapshot_id: "snapshot-1",
  workflow_status: "COMPILED_COMPLETE",
  contract_status: "COMPLETE",
  missing_fields: [],
  contradiction_reasons: [],
  activation_eligibility: true,
  channel_contract_json: suggestedContract,
  field_source_map_json: fieldSourceMap
};

function meta(value: unknown, sourceType: string, reviewRequired: boolean) {
  return {
    value,
    source_type: sourceType,
    confidence_label: sourceType === "UNKNOWN" ? "LOW" : "HIGH",
    evidence_refs: [],
    review_required: reviewRequired,
    editable_by_human: sourceType !== "GLOBAL_LOCKED_POLICY" && sourceType !== "PROVIDER_POLICY",
    locked_reason: sourceType === "GLOBAL_LOCKED_POLICY" || sourceType === "PROVIDER_POLICY" ? "Locked" : null
  };
}
