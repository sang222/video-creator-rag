import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FinalReviewSurface } from "@/features/production/final-review-surface";
import type { FinalReview } from "@/lib/types";

const candidateId = "123e4567-e89b-12d3-a456-426614174000";

const localReview: FinalReview = {
  candidate_id: candidateId,
  project_id: "123e4567-e89b-12d3-a456-426614174001",
  workflow_run_id: "123e4567-e89b-12d3-a456-426614174002",
  state: "READY_FOR_FINAL_REVIEW",
  title: "Video cuối đã xác minh",
  description: "Nội dung qualification.",
  lane: "LONG_FORM",
  content_mode: "STANDALONE",
  audience_promise: "Giúp người vận hành đưa ra quyết định có căn cứ.",
  strategic_intent: "ACQUISITION",
  standalone_reason: "Video độc lập",
  destination_label: "Kênh VCOS",
  media: {
    file_name: "final.mp4",
    player_url: `/final-review-candidates/${candidateId}/media`,
    thumbnail_url: `/final-review-candidates/${candidateId}/thumbnail`,
    captions_label: "Tệp phụ đề SRT rời đã xác minh trên Google Drive",
    caption_sidecar: {
      label: "Tệp phụ đề SRT rời đã xác minh trên Google Drive",
      file_name: "canonical-captions.srt",
      caption_ref: "artifact-version://caption/verified",
      archive_object_ref: "drive://caption-file/canonical-captions.srt",
      drive_web_view_url: "https://drive.google.com/file/d/caption-file/view",
      checksum_sha256: "b".repeat(64),
      caption_artifact_hash: "c".repeat(64),
      subtitle_qc_ref: "artifact-version://subtitle-qc/verified",
      subtitle_qc_hash: "d".repeat(64),
      cloud_media_ref_id: "123e4567-e89b-12d3-a456-426614174003",
      drive_file_id: "caption-file",
      verification_state: "VERIFIED",
      delivery_mode: "SIDECAR_ONLY"
    },
    checksum_sha256: "a".repeat(64),
    duration_seconds: 12
  },
  warnings: [],
  rights_disclosure_summary: "Quyền sử dụng đã được đóng gói.",
  auto_repair_summary: "Không có sửa đổi trọng yếu.",
  archive_status: "VERIFIED",
  technical_appendix: {}
};

describe("FinalReviewSurface local archive", () => {
  it("renders authenticated candidate-only player and thumbnail URLs", () => {
    render(<FinalReviewSurface review={localReview} />);

    expect(screen.getByLabelText("Video MP4 cuối để xem")).toHaveAttribute(
      "src",
      `http://localhost:8000/final-review-candidates/${candidateId}/media`
    );
    expect(
      screen.getByAltText("Thumbnail cho Video cuối đã xác minh")
    ).toHaveAttribute(
      "src",
      `http://localhost:8000/final-review-candidates/${candidateId}/thumbnail`
    );
    expect(screen.getByText("Cam kết với khán giả")).toBeInTheDocument();
    expect(screen.getByText("Thu hút đúng khán giả mục tiêu")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Mở tệp SRT sidecar trên Google Drive/i })
    ).toHaveAttribute(
      "href",
      "https://drive.google.com/file/d/caption-file/view"
    );
  });
});
