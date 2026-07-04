import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PackageReviewView } from "@/features/publishing/package-review-view";

const packageReview = {
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
  packaging_handoff: {
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
      promise_made: "No paid calls before approval",
      payoff_location: "S2",
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
      concept: "Provider boundary dashboard",
      text_overlay: "No paid calls",
      main_subject: "VCOS dashboard",
      composition: "Large text over operator panel",
      mobile_readability_notes: "Three words, high contrast.",
      thumbnail_ref: null,
      drive_ref: null,
      character_image_branch_id: null,
      reference_asset_pack_id: null,
      thumbnail_variant_plan_json: [{ concept: "Provider boundary dashboard", text: "No paid calls" }],
      contract_paths_used_json: ["thumbnail_style_context"],
      source_artifact_refs_json: []
    },
    publish_timing_recommendation: {
      channel_timezone: "America/New_York",
      audience_timezone: "America/New_York",
      operator_local_timezone: "Asia/Ho_Chi_Minh",
      configured_publish_window_json: { windows: [{ day: "MONDAY", start: "09:00", end: "11:00" }] },
      suggested_publish_time_channel_tz: "2026-07-06T09:00:00-04:00",
      suggested_publish_time_operator_local: "2026-07-06T20:00:00+07:00",
      publish_timing_policy_ref: "effective_context:effective-1:publish_timing_context",
      manual_publish_only: true,
      source_contract_paths: ["platform_strategy.publish_mode"],
      reason_codes_json: []
    },
    packaging_gate_summary: {
      overall_status: "REVIEW_REQUIRED",
      next_action_vi: "Review các gate cần kiểm tra trước khi upload thủ công.",
      r3d4_gate_batch_refs: [],
      gate_results: [
        {
          gate_key: "HookTruthfulnessGate",
          status: "PASS",
          reason_codes: [],
          checked_artifact_refs: [{ artifact_key: "hook_spec" }],
          checked_contract_paths: ["script_contract"],
          summary_vi: "Hook khớp nội dung script.",
          next_action_vi: null
        },
        {
          gate_key: "ThumbnailTruthfulnessGate",
          status: "PASS",
          reason_codes: [],
          checked_artifact_refs: [{ artifact_key: "thumbnail_brief" }],
          checked_contract_paths: ["thumbnail_style_context"],
          summary_vi: "Thumbnail brief không gây hiểu sai.",
          next_action_vi: null
        },
        {
          gate_key: "PublishTimingComplianceGate",
          status: "PASS",
          reason_codes: [],
          checked_artifact_refs: [{ artifact_key: "publish_timing" }],
          checked_contract_paths: ["publish_timing_context"],
          summary_vi: "Publish timing là recommendation thủ công.",
          next_action_vi: null
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
  }
};

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
  queryKeys: {
    videoPackageReview: (packageId: string) => ["video-package-review", packageId]
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

describe("PackageReviewView", () => {
  it("renders the M1 packaging handoff panels without unsafe job buttons", async () => {
    renderWithQuery();

    expect(await screen.findByText("Review hook / 3 giây đầu")).toBeInTheDocument();
    expect(screen.getByText("Copy upload sang YouTube")).toBeInTheDocument();
    expect(screen.getByText("Subtitle refs")).toBeInTheDocument();
    expect(screen.getByText("Disclosure notes")).toBeInTheDocument();
    expect(screen.getByText("Handoff thumbnail")).toBeInTheDocument();
    expect(screen.getAllByText("Provider boundary dashboard").length).toBeGreaterThan(0);
    expect(screen.getByText("Thời điểm publish khuyến nghị")).toBeInTheDocument();
    expect(screen.getAllByText("America/New_York").length).toBeGreaterThan(0);
    expect(screen.getByText(/Chỉ publish thủ công/)).toBeInTheDocument();
    expect(screen.getByText("Tóm tắt gate packaging")).toBeInTheDocument();
    expect(screen.getByText("HookTruthfulnessGate")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tạo task upload thủ công/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /daily/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /NoView/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /vector/i })).not.toBeInTheDocument();
  });
});
