import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PackageReviewView } from "@/features/publishing/package-review-view";
import { applyApprovedChangesAndRecheckPackage } from "@/lib/api";
import type { VideoPackageReview } from "@/lib/types";

const baseHandoff = {
  package_id: "pkg-12345678",
  package_status: "READY_FOR_HUMAN_REVIEW",
  channel_id: "channel-1",
  video_project_id: "project-1",
  effective_context_snapshot_id: "effective-1",
  effective_context_hash: "ctx-hash",
  hook_spec: {
    id: "hookspec:pkg",
    package_id: "pkg-12345678",
    video_project_id: "project-1",
    effective_context_snapshot_id: "effective-1",
    hook_type: "DIRECT",
    first_3_seconds_script: "VCOS prepares a safe package.",
    first_3_seconds_visual: "Operator dashboard provider boundary card",
    promise_made: null,
    payoff_location: null,
    clickbait_risk: "LOW",
    evidence_refs_json: [],
    contract_paths_used_json: ["platform_strategy.publish_mode"],
    content_hash: "hash",
    created_at: "2026-07-04T00:00:00Z"
  },
  upload_handoff_copy: {
    title: "VCOS packaging handoff",
    description: "Copy this description into YouTube manually after review.",
    hashtags_json: ["VCOS"],
    subtitle_refs_json: [{ ref: "caption:draft", lifecycle_state: "DRAFT_SCRIPT_TIMING" }],
    disclosure_notes_json: [{ text: "AI-assisted draft." }],
    checklist_items_json: [{ item: "Copy title", state: "PENDING" }],
    language: "vi",
    locale: "vi-VN",
    channel_contract_hash: "contract-hash",
    effective_context_snapshot_id: "effective-1",
    packaging_gate_status: "REVIEW_REQUIRED",
    source_artifact_refs_json: []
  },
  thumbnail_handoff: {
    concept: null,
    text_overlay: null,
    main_subject: null,
    composition: null,
    mobile_readability_notes: null,
    thumbnail_ref: null,
    drive_ref: null,
    character_image_branch_id: null,
    reference_asset_pack_id: null,
    thumbnail_variant_plan_json: null,
    contract_paths_used_json: ["thumbnail_style_context"],
    source_artifact_refs_json: []
  },
  publish_timing_recommendation: {
    channel_timezone: "America/New_York",
    audience_timezone: "America/New_York",
    operator_local_timezone: "Asia/Ho_Chi_Minh",
    configured_publish_window_json: null,
    suggested_publish_time_channel_tz: null,
    suggested_publish_time_operator_local: null,
    publish_timing_policy_ref: "effective_context:effective-1:publish_timing_context",
    manual_publish_only: true,
    source_contract_paths: ["platform_strategy.publish_mode"],
    reason_codes_json: ["PUBLISH_WINDOW_MISSING"]
  },
  packaging_gate_summary: {
    overall_status: "REVIEW_REQUIRED",
    next_action_vi: "Review các gate cần kiểm tra trước khi upload thủ công.",
    r3d4_gate_batch_refs: [],
    gate_results: [
      {
        gate_key: "HookTruthfulnessGate",
        status: "REVIEW_REQUIRED",
        reason_codes: ["HOOK_PROMISE_MISSING"],
        checked_artifact_refs: [{ artifact_key: "hook_spec" }],
        checked_contract_paths: ["script_contract"],
        summary_vi: "Thiếu promise của hook; cần người review.",
        next_action_vi: "Thiếu promise của hook; cần người review."
      },
      {
        gate_key: "ThumbnailTruthfulnessGate",
        status: "REVIEW_REQUIRED",
        reason_codes: ["THUMBNAIL_BRIEF_MISSING"],
        checked_artifact_refs: [{ artifact_key: "thumbnail_brief" }],
        checked_contract_paths: ["thumbnail_style_context"],
        summary_vi: "Thiếu thumbnail brief.",
        next_action_vi: "Thiếu thumbnail brief."
      },
      {
        gate_key: "PublishTimingComplianceGate",
        status: "REVIEW_REQUIRED",
        reason_codes: ["PUBLISH_WINDOW_MISSING"],
        checked_artifact_refs: [{ artifact_key: "publish_timing" }],
        checked_contract_paths: ["publish_timing_context"],
        summary_vi: "Thiếu khung giờ publish trong frozen context.",
        next_action_vi: "Thiếu khung giờ publish trong frozen context."
      }
    ]
  },
  manual_upload: {
    human_upload_task_id: null,
    task_status: null,
    youtube_video_id: null,
    next_action_vi: "Upload thủ công trên YouTube rồi nhập URL/video_id vào VCOS."
  },
  provider_readiness_summary: {},
  manual_publish_only: true,
  no_upload_or_publish_calls_made: true,
  created_at: "2026-07-04T00:00:00Z"
};

const reviewRequired: VideoPackageReview = {
  package_id: "pkg-12345678",
  package_status: "READY_FOR_HUMAN_REVIEW",
  channel_binding: {},
  effective_context: {},
  human_review_checklist: {},
  agent_outputs: {},
  prompt_snapshots: {},
  provider_readiness_snapshot_ref: null,
  limitations: [],
  next_action: "Human final approval required.",
  packaging_handoff: baseHandoff,
  packaging_review_queue: {
    package_id: "pkg-12345678",
    review_verdict: "REVIEW_REQUIRED",
    plain_language_status: "Còn mục cần review trước upload.",
    must_fix_count: 3,
    next_safe_action: "Duyệt, reject hoặc request changes trên proposed patch.",
    upload_task_creation_allowed: false,
    approved_patch_count: 0,
    ready_for_review_patch_count: 1,
    rejected_patch_count: 0,
    request_changes_patch_count: 0,
    applied_patch_count: 0,
    can_apply_approved_changes: false,
    apply_approved_changes_label: "Chưa có patch được duyệt",
    apply_approved_changes_disabled_reason: "Chưa có patch được duyệt",
    last_apply_recheck_result: null,
    technical_appendix: {},
    items: [
      {
        id: "queue-hook",
        package_id: "pkg-12345678",
        video_project_id: "project-1",
        effective_context_snapshot_id: "effective-1",
        gate_key: "HookTruthfulnessGate",
        issue_code: "HOOK_PROMISE_MISSING",
        severity: "REVIEW_REQUIRED",
        target_artifact_type: "hook_spec",
        target_artifact_ref: "hook_spec",
        source_gate_run_id: null,
        source_gate_batch_id: null,
        status: "PENDING_PATCH",
        next_action_code: "NEEDS_PROPOSED_PATCH",
        human_readable_title: "Hook thiếu promise rõ ràng",
        human_readable_why: "Người xem chưa biết video hứa trả lời điều gì.",
        human_readable_fix: "Duyệt patch bổ sung promise và payoff location cho hook.",
        section: "Hook Review",
        proposed_patch: null,
        created_at: "2026-07-04T00:00:00Z",
        updated_at: "2026-07-04T00:00:00Z"
      },
      {
        id: "queue-thumbnail",
        package_id: "pkg-12345678",
        video_project_id: "project-1",
        effective_context_snapshot_id: "effective-1",
        gate_key: "ThumbnailTruthfulnessGate",
        issue_code: "THUMBNAIL_BRIEF_MISSING",
        severity: "REVIEW_REQUIRED",
        target_artifact_type: "thumbnail_brief",
        target_artifact_ref: "thumbnail_brief",
        source_gate_run_id: null,
        source_gate_batch_id: null,
        status: "PENDING_PATCH",
        next_action_code: "NEEDS_PROPOSED_PATCH",
        human_readable_title: "Thiếu thumbnail brief",
        human_readable_why: "Chưa có concept/overlay/subject để human tạo thumbnail.",
        human_readable_fix: "Duyệt patch thumbnail brief.",
        section: "Thumbnail Handoff",
        proposed_patch: null,
        created_at: "2026-07-04T00:00:00Z",
        updated_at: "2026-07-04T00:00:00Z"
      },
      {
        id: "queue-publish",
        package_id: "pkg-12345678",
        video_project_id: "project-1",
        effective_context_snapshot_id: "effective-1",
        gate_key: "PublishTimingComplianceGate",
        issue_code: "PUBLISH_WINDOW_MISSING",
        severity: "REVIEW_REQUIRED",
        target_artifact_type: "publish_timing",
        target_artifact_ref: "publish_timing",
        source_gate_run_id: null,
        source_gate_batch_id: null,
        status: "PENDING_HUMAN_REVIEW",
        next_action_code: "REVIEW_PROPOSED_PATCH",
        human_readable_title: "Thiếu publish window",
        human_readable_why: "VCOS chưa có khung giờ publish khuyến nghị theo frozen context.",
        human_readable_fix: "Duyệt package-level ManualPublishTimingOverride. Không mutate Channel Contract.",
        section: "Publish Timing",
        proposed_patch: {
          id: "patch-publish",
          queue_item_id: "queue-publish",
          package_id: "pkg-12345678",
          proposal_source: "DETERMINISTIC_SERVICE",
          routed_agent_key: null,
          patch_type: "PUBLISH_TIMING_OVERRIDE",
          before_snapshot_ref: "package:before",
          proposed_patch_json: { operation: "create_manual_publish_timing_override" },
          after_preview_json: { manual_publish_timing_override: { publish_window_state: "NEEDS_HUMAN_SELECTION" } },
          affected_artifact_refs_json: [{ artifact_key: "publish_timing" }],
          risk_level: "LOW",
          requires_human_approval: true,
          patch_hash: "patch-hash",
          status: "READY_FOR_REVIEW",
          created_at: "2026-07-04T00:00:00Z"
        },
        created_at: "2026-07-04T00:00:00Z",
        updated_at: "2026-07-04T00:00:00Z"
      }
    ]
  }
};

let packageReview: VideoPackageReview;

vi.mock("@/lib/api", () => ({
  getVideoPackageReview: vi.fn(async () => packageReview),
  createUploadTaskFromPackage: vi.fn(async () => ({
    id: "task-12345678",
    next_action: "Upload thủ công.",
    title_snapshot: "VCOS packaging handoff",
    status: "READY_FOR_HUMAN_UPLOAD",
    channel_id: "channel-1",
    destination: "YOUTUBE",
    subtitle_refs: [],
    required_assets: [],
    checklist: [],
    created_at: "2026-07-04T00:00:00Z",
    updated_at: "2026-07-04T00:00:00Z"
  })),
  buildPackagingReviewQueueFromGates: vi.fn(async () => reviewRequired.packaging_review_queue),
  applyApprovedChangesAndRecheckPackage: vi.fn(async () => ({
    status: "APPLIED_AND_RECHECKED",
    package_id: "pkg-12345678",
    applied_patch_ids: ["patch-publish"],
    skipped_patch_ids: [],
    gate_rerun_record_ids: ["rerun-1"],
    package_status: "READY_FOR_HUMAN_REVIEW",
    final_package_status: "READY_FOR_HUMAN_REVIEW",
    review_verdict: "REVIEW_REQUIRED",
    must_fix_count: 2,
    upload_task_creation_allowed: false,
    remaining_blockers: [{ issue_code: "HOOK_PROMISE_MISSING" }],
    next_safe_action: "Operator reviews remaining queue items.",
    no_provider_media_upload_execution: true,
    no_execution_proof: { no_upload_task_created: true }
  })),
  approvePackagingProposedPatch: vi.fn(async () => ({ id: "decision-1", proposed_patch_id: "patch-publish", decision: "APPROVE", decided_by: "operator", created_at: "2026-07-04T00:00:00Z" })),
  rejectPackagingProposedPatch: vi.fn(async () => ({ id: "decision-2", proposed_patch_id: "patch-publish", decision: "REJECT", decided_by: "operator", created_at: "2026-07-04T00:00:00Z" })),
  requestChangesPackagingProposedPatch: vi.fn(async () => ({ id: "decision-3", proposed_patch_id: "patch-publish", decision: "REQUEST_CHANGES", decided_by: "operator", created_at: "2026-07-04T00:00:00Z" })),
  queryKeys: {
    videoPackageReview: (packageId: string) => ["video-package-review", packageId],
    packagingReviewQueue: (packageId: string) => ["packaging-review-queue", packageId]
  }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PackageReviewView packageId="pkg-12345678" />
    </QueryClientProvider>
  );
}

function setApplyState({
  approved,
  ready,
  canApply,
  label,
  disabledReason
}: {
  approved: number;
  ready: number;
  canApply: boolean;
  label: string;
  disabledReason?: string | null;
}) {
  const queue = packageReview.packaging_review_queue;
  if (!queue) throw new Error("missing queue fixture");
  queue.approved_patch_count = approved;
  queue.ready_for_review_patch_count = ready;
  queue.can_apply_approved_changes = canApply;
  queue.apply_approved_changes_label = label;
  queue.apply_approved_changes_disabled_reason = disabledReason ?? null;
}

describe("PackageReviewView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    packageReview = structuredClone(reviewRequired);
  });

  it("shows review verdict, must-fix queue and human-readable actions", async () => {
    renderWithQuery();

    expect(await screen.findByText("Must Fix Before Upload")).toBeInTheDocument();
    expect(screen.getByText("Còn mục cần review trước upload.")).toBeInTheDocument();
    expect(screen.getByText("Hook thiếu promise rõ ràng")).toBeInTheDocument();
    expect(screen.getByText("Người xem chưa biết video hứa trả lời điều gì.")).toBeInTheDocument();
    expect(screen.getByText("Duyệt patch bổ sung promise và payoff location cho hook.")).toBeInTheDocument();
    expect(screen.getByText("Thiếu thumbnail brief")).toBeInTheDocument();
    expect(screen.getByText("Duyệt patch thumbnail brief.")).toBeInTheDocument();
    expect(screen.getByText("Thiếu publish window")).toBeInTheDocument();
    expect(screen.getByText("Duyệt package-level ManualPublishTimingOverride. Không mutate Channel Contract.")).toBeInTheDocument();
  });

  it("shows approval buttons only for ready proposed patch and disables upload task", async () => {
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Còn mục cần review/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reject/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Request changes/ })).toBeInTheDocument();
    expect(screen.getAllByText("Đang cần proposed patch").length).toBe(2);
  });

  it("disables apply/recheck when no patch has been approved", async () => {
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Chưa có patch được duyệt/ })).toBeDisabled();
    expect(screen.getByText("Áp dụng các patch đã được duyệt và kiểm tra lại package.")).toBeInTheDocument();
  });

  it("disables apply/recheck while a patch is still waiting for a decision", async () => {
    setApplyState({
      approved: 1,
      ready: 1,
      canApply: false,
      label: "Còn patch chưa quyết định",
      disabledReason: "Còn patch chưa quyết định"
    });
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Còn patch chưa quyết định/ })).toBeDisabled();
  });

  it("enables apply/recheck when approved patches exist and all current patches have decisions", async () => {
    setApplyState({
      approved: 1,
      ready: 0,
      canApply: true,
      label: "Apply approved changes & recheck package",
      disabledReason: null
    });
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Apply approved changes & recheck package/ })).toBeEnabled();
  });

  it("clicking apply/recheck calls the package endpoint", async () => {
    setApplyState({
      approved: 1,
      ready: 0,
      canApply: true,
      label: "Apply approved changes & recheck package",
      disabledReason: null
    });
    const user = userEvent.setup();
    renderWithQuery();

    await user.click(await screen.findByRole("button", { name: /Apply approved changes & recheck package/ }));

    await waitFor(() => expect(applyApprovedChangesAndRecheckPackage).toHaveBeenCalledWith("pkg-12345678"));
  });

  it("renders loading and result state for apply/recheck", async () => {
    setApplyState({
      approved: 1,
      ready: 0,
      canApply: true,
      label: "Apply approved changes & recheck package",
      disabledReason: null
    });
    let resolveApply: (value: Awaited<ReturnType<typeof applyApprovedChangesAndRecheckPackage>>) => void = () => {};
    vi.mocked(applyApprovedChangesAndRecheckPackage).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveApply = resolve;
        })
    );
    const user = userEvent.setup();
    renderWithQuery();

    await user.click(await screen.findByRole("button", { name: /Apply approved changes & recheck package/ }));

    expect(screen.getByRole("button", { name: /Đang apply và kiểm tra lại/ })).toBeDisabled();
    resolveApply({
      status: "APPLIED_AND_RECHECKED",
      package_id: "pkg-12345678",
      applied_patch_ids: ["patch-publish"],
      skipped_patch_ids: [],
      gate_rerun_record_ids: ["rerun-1"],
      package_status: "READY_FOR_HUMAN_REVIEW",
      final_package_status: "READY_FOR_HUMAN_REVIEW",
      review_verdict: "REVIEW_REQUIRED",
      must_fix_count: 2,
      upload_task_creation_allowed: false,
      remaining_blockers: [{ issue_code: "HOOK_PROMISE_MISSING" }],
      next_safe_action: "Operator reviews remaining queue items.",
      no_provider_media_upload_execution: true,
      no_execution_proof: { no_upload_task_created: true }
    });

    expect(await screen.findByText("Đã apply patch được duyệt và kiểm tra lại.")).toBeInTheDocument();
    expect(screen.getAllByText("1 mục").length).toBeGreaterThan(0);
    expect(screen.getByText("Operator reviews remaining queue items.")).toBeInTheDocument();
  });

  it("keeps raw gate table collapsed under technical details", async () => {
    renderWithQuery();

    expect(await screen.findByText("Chi tiết kỹ thuật")).toBeInTheDocument();
    const details = screen.getByText("Chi tiết kỹ thuật").closest("details");
    expect(details).not.toHaveAttribute("open");
  });

  it("keeps upload task disabled when review passes but final video is missing", async () => {
    packageReview = {
      ...structuredClone(reviewRequired),
      packaging_handoff: {
        ...structuredClone(baseHandoff),
        packaging_gate_summary: { ...baseHandoff.packaging_gate_summary, overall_status: "PASS" }
      },
      packaging_review_queue: {
        package_id: "pkg-12345678",
        review_verdict: "WAITING_FINAL_MEDIA_ASSET",
        plain_language_status: "Package review đã pass; chưa có video final để upload.",
        must_fix_count: 0,
        next_safe_action: "Cần tạo hoặc đính kèm final video asset đã verify trước khi tạo task upload.",
        upload_task_creation_allowed: false,
        approved_patch_count: 0,
        ready_for_review_patch_count: 0,
        rejected_patch_count: 0,
        request_changes_patch_count: 0,
        applied_patch_count: 0,
        can_apply_approved_changes: false,
        apply_approved_changes_label: "Không có thay đổi cần apply",
        apply_approved_changes_disabled_reason: "Không có thay đổi cần apply",
        last_apply_recheck_result: null,
        technical_appendix: { has_uploadable_final_media: false },
        items: []
      }
    };
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Chưa có video final/ })).toBeDisabled();
    expect(screen.getByText("Trạng thái review")).toBeInTheDocument();
    expect(screen.getAllByText("Chờ video final").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Đã sẵn sàng").length).toBeGreaterThan(0);
    expect(screen.getByText("Package review đã pass; chưa có video final để upload.")).toBeInTheDocument();
  });

  it("enables upload task only when all required review items are closed/pass", async () => {
    packageReview = {
      ...structuredClone(reviewRequired),
      packaging_handoff: {
        ...structuredClone(baseHandoff),
        packaging_gate_summary: { ...baseHandoff.packaging_gate_summary, overall_status: "PASS" }
      },
      packaging_review_queue: {
        package_id: "pkg-12345678",
        review_verdict: "READY_FOR_MANUAL_UPLOAD",
        plain_language_status: "Sẵn sàng tạo task upload thủ công.",
        must_fix_count: 0,
        next_safe_action: "Có thể tạo task upload thủ công; VCOS vẫn không upload/publish.",
        upload_task_creation_allowed: true,
        approved_patch_count: 0,
        ready_for_review_patch_count: 0,
        rejected_patch_count: 0,
        request_changes_patch_count: 0,
        applied_patch_count: 0,
        can_apply_approved_changes: false,
        apply_approved_changes_label: "Không có thay đổi cần apply",
        apply_approved_changes_disabled_reason: "Không có thay đổi cần apply",
        last_apply_recheck_result: null,
        technical_appendix: {},
        items: []
      }
    };
    renderWithQuery();

    expect(await screen.findByRole("button", { name: /Tạo task upload thủ công/ })).toBeEnabled();
  });

  it("does not add provider/render/upload/YouTube execution buttons", async () => {
    renderWithQuery();

    expect(await screen.findByText("Must Fix Before Upload")).toBeInTheDocument();
    [
      /Generate video/i,
      /^Render$/i,
      /Run provider/i,
      /Execute provider/i,
      /Upload YouTube/i,
      /^Publish$/i,
      /Auto publish/i,
      /Run scheduled generation/i,
      /Run vector/i,
      /Run NoView scanner/i
    ].forEach((name) => {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /scheduled generation/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /NoView/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /vector/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /render/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /YouTube upload/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /provider/i })).not.toBeInTheDocument();
  });
});
