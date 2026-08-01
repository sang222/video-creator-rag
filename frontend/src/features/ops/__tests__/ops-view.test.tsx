import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OpsView } from "@/features/ops/ops-view";

const apiMocks = vi.hoisted(() => ({
  getRuntimeOpsCommandCenter: vi.fn(),
  getChannelRuntimeTrace: vi.fn(),
  getPackageOpsSummary: vi.fn(),
  getUploadedVideoOpsSummary: vi.fn(),
  getDiagnosticsQueue: vi.fn(),
  getRecoveryQueue: vi.fn(),
  getLearningOpsQueue: vi.fn(),
  getMemoryOpsQueue: vi.fn(),
  getRetrievalManifestOps: vi.fn(),
  getMemoryInfluenceOps: vi.fn(),
  getQualityDeltaOps: vi.fn(),
  getProviderCostOps: vi.fn()
}));

const navigationMocks = vi.hoisted(() => ({
  searchParams: new URLSearchParams(
    "channel=channel-1&package=package-1&uploaded=uploaded-1&retrieval=manifest-1&memory_influence=influence-1&quality_delta=quality-1"
  )
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => navigationMocks.searchParams
}));

vi.mock("@/lib/api", () => ({
  ...apiMocks,
  queryKeys: {
    runtimeOpsCommandCenter: ["runtime-ops-command-center"],
    channelRuntimeTrace: (channelId: string) => ["channel-runtime-trace", channelId],
    packageOpsSummary: (packageId: string) => ["package-ops-summary", packageId],
    uploadedVideoOpsSummary: (uploadedVideoId: string) => ["uploaded-video-ops-summary", uploadedVideoId],
    diagnosticsQueue: ["diagnostics-queue"],
    recoveryQueue: ["recovery-queue"],
    learningOpsQueue: ["learning-ops-queue"],
    memoryOpsQueue: ["memory-ops-queue"],
    retrievalManifestOps: (manifestId: string) => ["retrieval-manifest-ops", manifestId],
    memoryInfluenceOps: (manifestId: string) => ["memory-influence-ops", manifestId],
    qualityDeltaOps: (qualityDeltaId: string) => ["quality-delta-ops", qualityDeltaId],
    providerCostOps: (packageId: string) => ["provider-cost-ops", packageId]
  }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OpsView />
    </QueryClientProvider>
  );
}

describe("OpsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigationMocks.searchParams = new URLSearchParams(
      "channel=channel-1&package=package-1&uploaded=uploaded-1&retrieval=manifest-1&memory_influence=influence-1&quality_delta=quality-1"
    );
    apiMocks.getRuntimeOpsCommandCenter.mockResolvedValue(commandCenterPayload);
    apiMocks.getChannelRuntimeTrace.mockResolvedValue(runtimeTracePayload);
    apiMocks.getPackageOpsSummary.mockResolvedValue(packageOpsPayload);
    apiMocks.getUploadedVideoOpsSummary.mockResolvedValue(uploadedVideoPayload);
    apiMocks.getDiagnosticsQueue.mockResolvedValue(queuePayload("Diagnostic quá sớm", "WAIT_ANALYTICS_MATURITY"));
    apiMocks.getRecoveryQueue.mockResolvedValue(queuePayload("Đề xuất recovery thumbnail", "REVIEW_RECOVERY_PROPOSAL"));
    apiMocks.getLearningOpsQueue.mockResolvedValue(queuePayload("Learning candidate hook", "REVIEW_LEARNING_CANDIDATE"));
    apiMocks.getMemoryOpsQueue.mockResolvedValue({
      ...queuePayload("Memory item cần duyệt", "REVIEW_MEMORY_ITEM"),
      prompt_eligibility_rule_vi: "Chỉ APPROVED + SAFE + PROMPT_SAFE + FRESH mới được prompt-eligible."
    });
    apiMocks.getRetrievalManifestOps.mockResolvedValue(retrievalManifestPayload);
    apiMocks.getMemoryInfluenceOps.mockResolvedValue(memoryInfluencePayload);
    apiMocks.getQualityDeltaOps.mockResolvedValue(qualityDeltaPayload);
    apiMocks.getProviderCostOps.mockResolvedValue(providerCostPayload);
  });

  it("renders the R3D9 cockpit panels without job-control or provider execution buttons", async () => {
    renderWithQuery();

    expect(await screen.findByRole("heading", { name: "Runtime Dashboard Ops" })).toBeInTheDocument();
    expect(screen.getByText("Gói chờ review")).toBeInTheDocument();
    expect(screen.getByText("Upload thủ công")).toBeInTheDocument();
    expect(await screen.findByText("Channel Runtime Trace")).toBeInTheDocument();
    expect(await screen.findByText("Package Ops Summary")).toBeInTheDocument();
    expect(await screen.findByText("Uploaded Video Monitor")).toBeInTheDocument();
    expect(await screen.findByText("Diagnostic / Recovery")).toBeInTheDocument();
    expect(await screen.findByText("Learning Review Queue")).toBeInTheDocument();
    expect(await screen.findByText("Memory Approval Queue")).toBeInTheDocument();
    expect(await screen.findByText("Retrieval Manifest Debug")).toBeInTheDocument();
    expect(await screen.findByText("Memory Influence")).toBeInTheDocument();
    expect(await screen.findByText("Quality Delta")).toBeInTheDocument();
    expect(await screen.findByText("Provider / Cost Firewall")).toBeInTheDocument();
    expect(screen.getByText("Đã ẩn mặc định")).toBeInTheDocument();
    expect(screen.getByText("Không")).toBeInTheDocument();

    const providerPanel = screen.getByText("Provider / Cost Firewall").closest("section");
    expect(providerPanel).not.toBeNull();
    expect(within(providerPanel as HTMLElement).getByText("Không")).toBeInTheDocument();

    for (const button of screen.queryAllByRole("button")) {
      expect(button).not.toHaveTextContent(/scheduler|scanner|learning|provider|render|upload|youtube|chạy|run/i);
    }
  });
});

function nextAction(code: string) {
  return {
    next_action_code: code,
    next_action_label_vi: `Hành động: ${code}`,
    allowed_actor_role: "operator",
    blocking_reason_codes: [`${code}_REASON`],
    target_url: "/ops",
    action_ref: { code },
    is_manual_only: true
  };
}

function card(entityType: string, entityId: string, title: string, actionCode: string) {
  return {
    key: `${entityType}:${entityId}`,
    entity_type: entityType,
    entity_id: entityId,
    title,
    status: "ACTION_REQUIRED",
    severity: "MEDIUM",
    blocker_reason_codes: [`${actionCode}_REASON`],
    next_action: nextAction(actionCode),
    owner_role: "operator",
    link_target: "/ops",
    updated_at: "2026-07-04T00:00:00Z",
    technical_appendix: {}
  };
}

function queuePayload(summary: string, actionCode: string) {
  return {
    generated_at: "2026-07-04T00:00:00Z",
    items: [
      {
        id: `${actionCode.toLowerCase()}-1`,
        operator_summary: summary,
        approval_status: "PENDING",
        next_action: nextAction(actionCode)
      }
    ]
  };
}

const commandCenterPayload = {
  generated_at: "2026-07-04T00:00:00Z",
  active_channels: [card("channel", "channel-1", "Channel active", "VIEW_RUNTIME_TRACE")],
  packages_waiting_review: [card("package", "package-1", "Package cần review", "REVIEW_PACKAGE")],
  upload_tasks_waiting_human: [card("upload_task", "upload-task-1", "Manual upload task", "MANUAL_UPLOAD_OUTSIDE_VCOS")],
  uploaded_videos_waiting_verification_or_analytics: [card("uploaded_video", "uploaded-1", "Video cần verify", "VERIFY_UPLOADED_VIDEO")],
  diagnostics_needing_review: [card("diagnostic", "diagnostic-1", "Diagnostic quá sớm", "WAIT_ANALYTICS_MATURITY")],
  recovery_proposals_needing_action: [card("recovery", "recovery-1", "Recovery proposal", "REVIEW_RECOVERY_PROPOSAL")],
  learning_candidates_needing_review: [card("learning", "learning-1", "Learning candidate", "REVIEW_LEARNING_CANDIDATE")],
  memory_approvals_needing_review: [card("memory", "memory-1", "Memory item", "REVIEW_MEMORY_ITEM")],
  provider_cost_blockers: [card("provider_cost", "package-1", "Provider cost blocker", "BLOCKED_BY_PROVIDER_BOUNDARY")],
  gate_failures: [card("gate", "gate-1", "Gate fail", "REVIEW_PACKAGE")],
  next_actions: [nextAction("REVIEW_PACKAGE"), nextAction("BACKFILL_VIDEO_ID")],
  forbidden_actions: ["RUN_SCHEDULED_GENERATION", "RUN_NOVIEW_SCANNER", "RUN_VECTOR_LEARNING", "EXECUTE_PROVIDER", "YOUTUBE_UPLOAD"],
  technical_appendix: { no_provider_media_upload_execution: true }
};

const runtimeTracePayload = {
  channel_id: "channel-1",
  video_project_id: "project-1",
  package_id: "package-1",
  channel_profile_version_id: "cpv-1",
  compiled_policy_snapshot_id: "policy-1",
  channel_contract_hash: "contract-hash-123456",
  effective_context_snapshot_id: "context-1",
  context_hash: "context-hash-123456",
  category_id: "category-1",
  character_binding_id: null,
  market_locale_language: { market: "US", locale: "en-US", language: "en" },
  voice_profile: { provider_key: "elevenlabs" },
  thumbnail_style: { style: "clean" },
  publish_timing_policy: { window: "manual" },
  provider_boundary: { will_execute: false },
  budget_cost_policy: { monthly_cap_usd: 250 },
  source_refs: [{ type: "EffectiveChannelRuntimeContextSnapshot", id: "context-1" }],
  snapshot_refs: { effective_context_snapshot_id: "context-1" },
  latest_mutable_settings_used: false,
  technical_appendix: {}
};

const packageOpsPayload = {
  package_id: "package-1",
  package_status: "WAITING_REVIEW",
  video_project_id: "project-1",
  channel_id: "channel-1",
  effective_context_snapshot_id: "context-1",
  effective_context_hash: "context-hash-123456",
  agent_context_pack_refs: [{ id: "agent-pack-1" }],
  prompt_budget_summary: [{ agent_key: "hook_agent", budget_tokens: 1200 }],
  hook_first_3_seconds: { hook: "Mở bằng tương phản rõ" },
  title_description_subtitles_disclosure: { title: "Demo title", description: "Demo description", subtitles: ["srt"], disclosure: "AI-assisted" },
  thumbnail_handoff: { thumbnail_ref: "thumb-1" },
  publish_timing_recommendation: { recommended_window: "manual" },
  r3d4_deterministic_gate_results: [{ gate: "policy", result: "PASS" }],
  gatekeeper_soft_review_result: { result: "SOFT_REVIEW" },
  packaging_gate_results: [{ gate: "m1_package", result: "PASS" }],
  provider_boundary_summary: { will_execute: false },
  manual_publish_handoff: { manual_only_warning: true, checklist: ["Upload ngoài VCOS"] },
  next_action: nextAction("REVIEW_PACKAGE"),
  no_provider_media_upload_execution: true,
  technical_appendix: {}
};

const uploadedVideoPayload = {
  uploaded_video_id: "uploaded-1",
  platform: "youtube",
  platform_video_id: "yt123",
  platform_url: "https://youtu.be/yt123",
  backfill_history: [{ event: "BACKFILLED" }],
  verification_status: "PENDING_VERIFY",
  actual_upload_time: "2026-07-04T01:00:00Z",
  actual_publish_time: null,
  channel_timezone: "America/New_York",
  operator_timezone: "Asia/Ho_Chi_Minh",
  analytics_sync_status: "WAITING",
  analytics_maturity: "TOO_EARLY",
  analytics_confidence: "LOW",
  enforcement_restriction_flags: [],
  linked_package_project: { package_id: "package-1", project_id: "project-1" },
  diagnostics: [{ id: "diagnostic-1" }],
  recovery_proposal_refs: [{ id: "recovery-1" }],
  learning_candidate_refs: [{ id: "learning-1" }],
  next_action: nextAction("VERIFY_UPLOADED_VIDEO"),
  no_youtube_studio_scraping: true,
  technical_appendix: {}
};

const retrievalManifestPayload = {
  manifest_id: "manifest-1",
  effective_context_snapshot_id: "context-1",
  agent_key: "hook_agent",
  use_case: "SCRIPT_CONTEXT",
  sql_filter: { channel_id: "channel-1" },
  candidate_count_before_vector: 12,
  candidate_count_after_policy: 4,
  selected_facets: [{ facet_id: "facet-1" }],
  blocked_refs: [{ facet_id: "facet-blocked" }],
  rejected_refs: [{ facet_id: "facet-rejected" }],
  retrieval_hash: "retrieval-hash",
  digest_hash: "digest-hash",
  raw_memory_hidden: true,
  advanced_refs_collapsed_by_default: true,
  technical_appendix: {}
};

const memoryInfluencePayload = {
  manifest_id: "influence-1",
  video_project_id: "project-1",
  package_id: "package-1",
  agent_key: "hook_agent",
  retrieval_manifest_id: "manifest-1",
  memory_facets_used: [{ facet_id: "facet-1" }],
  digest_hash: "digest-hash",
  prompt_context_hash: "prompt-context-hash",
  applied_as: { mode: "context_digest" },
  ignored_memory_refs: [],
  blocked_memory_refs: [{ facet_id: "facet-blocked" }],
  scope_status: "IN_SCOPE",
  next_action: nextAction("VIEW_RETRIEVAL_MANIFEST"),
  technical_appendix: {}
};

const qualityDeltaPayload = {
  quality_delta_id: "quality-1",
  memory_facets_used: [{ facet_id: "facet-1" }],
  expected_metric_family: "RETENTION",
  expected_direction: "UP",
  baseline_snapshot: { retention: 0.42 },
  observed_snapshot: { retention: 0.48 },
  result: "IMPROVED",
  confidence_delta: 0.12,
  reason_codes: ["ENOUGH_DATA"],
  next_action: nextAction("VIEW_QUALITY_DELTA"),
  technical_appendix: {}
};

const providerCostPayload = {
  package_id: "package-1",
  provider_readiness: { drift_guard_status: "PASS", providers: ["elevenlabs", "google_veo", "pexels_api"] },
  missing_config: [],
  render_revisions: [{ id: "render-revision-1", provider_key: "native_ffmpeg_renderer" }],
  cost_estimates: [{ id: "cost-1", provider_key: "google_veo" }],
  human_paid_render_approvals: [{ id: "approval-1", status: "PENDING" }],
  paid_attempt_limits: [{ provider_key: "google_veo", attempt_count: 0 }],
  provider_boundary_decisions: [{ decision: "ALLOWED_NOT_EXECUTED" }],
  paid_provider_call_ledger: [],
  proxy_preview_flags: [{ will_execute: false }],
  will_execute: false,
  next_action: nextAction("WAIT_HUMAN_PAID_APPROVAL"),
  technical_appendix: { no_provider_media_upload_execution: true }
};
