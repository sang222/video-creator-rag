import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ManualPublishSurface } from "@/features/production/manual-publish-surface";
import type { ManualPublish } from "@/lib/types";

const submittedPublish: ManualPublish = {
  task_id: "task-1",
  project_id: "project-1",
  final_review_candidate_id: "candidate-1",
  state: "AWAITING_CONFIRMATION",
  exact_file_name: "final.mp4",
  drive_web_view_url: "https://drive.google.com/file/d/final/view",
  reviewed_checksum_sha256: "a".repeat(64),
  target_platform: "YOUTUBE",
  destination_label: "Kênh VCOS",
  destination_channel_id: "UC_VCOS",
  destination_handle: "@vcos",
  platform_video_id: "video-1",
  platform_video_url: "https://www.youtube.com/watch?v=video-1",
  actual_title: "Video cuối",
  actual_description: "Mô tả thực tế",
  actual_visibility: "PUBLIC",
  actual_published_at: "2026-07-29T10:00:00Z",
  actual_duration_seconds: 300,
  mismatch_state: "MATCHED",
  correction_state: "NOT_REQUIRED",
  uploaded_video_status: "NOT_RECORDED",
  analytics_ready: false,
  next_action: "Xác minh confirmation để tạo UploadedVideo có lineage đầy đủ.",
  technical_appendix: {
    confirmation_id: "confirmation-1",
    confirmation_state: "SUBMITTED"
  }
};

describe("ManualPublishSurface verification", () => {
  it("offers the supported verification action after confirmation", async () => {
    const onVerify = vi.fn();
    render(
      <ManualPublishSurface
        onVerify={onVerify}
        publish={submittedPublish}
      />
    );

    await userEvent.click(
      screen.getByRole("button", {
        name: "Đã đối chiếu, tạo UploadedVideo"
      })
    );

    expect(onVerify).toHaveBeenCalledOnce();
  });

  it("does not offer verification before a submitted confirmation exists", () => {
    render(
      <ManualPublishSurface
        onVerify={vi.fn()}
        publish={{
          ...submittedPublish,
          technical_appendix: {
            confirmation_id: null,
            confirmation_state: null
          }
        }}
      />
    );

    expect(
      screen.queryByRole("button", {
        name: "Đã đối chiếu, tạo UploadedVideo"
      })
    ).not.toBeInTheDocument();
  });

  it("offers the authenticated verified archive download when Drive is absent", () => {
    render(
      <ManualPublishSurface
        publish={{
          ...submittedPublish,
          drive_web_view_url: null,
          verified_file_download_url:
            "/final-review-candidates/123e4567-e89b-12d3-a456-426614174000/media?download=1"
        }}
      />
    );

    expect(
      screen.getByRole("link", { name: /Tải file đã xác minh/ })
    ).toHaveAttribute(
      "href",
      "http://localhost:8000/final-review-candidates/123e4567-e89b-12d3-a456-426614174000/media?download=1"
    );
  });
});
