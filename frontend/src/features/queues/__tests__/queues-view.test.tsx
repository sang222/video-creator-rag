import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueuesView } from "@/features/queues/queues-view";

const apiMocks = vi.hoisted(() => ({
  getQueues: vi.fn(),
  approvePackagingProposedPatch: vi.fn(),
  rejectPackagingProposedPatch: vi.fn(),
  requestChangesPackagingProposedPatch: vi.fn()
}));

vi.mock("@/lib/api", () => ({
  ...apiMocks,
  queryKeys: {
    queues: (queueType?: string) => ["queues", queueType ?? "all"],
    videoPackageReview: (packageId: string) => ["video-package-review", packageId],
    packagingReviewQueue: (packageId: string) => ["packaging-review-queue", packageId]
  }
}));

function renderWithQuery(queueType?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueuesView queueType={queueType} />
    </QueryClientProvider>
  );
}

describe("QueuesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getQueues.mockResolvedValue(packagingQueuePayload());
    apiMocks.approvePackagingProposedPatch.mockResolvedValue({ id: "decision-approve", decision: "APPROVE" });
    apiMocks.rejectPackagingProposedPatch.mockResolvedValue({ id: "decision-reject", decision: "REJECT" });
    apiMocks.requestChangesPackagingProposedPatch.mockResolvedValue({ id: "decision-changes", decision: "REQUEST_CHANGES" });
  });

  it("calls the packaging approve endpoint from dashboard queue cards", async () => {
    const user = userEvent.setup();
    renderWithQuery();

    await user.click(await screen.findByRole("button", { name: "Duyệt" }));

    await waitFor(() => {
      expect(apiMocks.approvePackagingProposedPatch).toHaveBeenCalledWith("patch-1", "Duyệt từ hàng chờ duyệt.");
    });
    expect(await screen.findByText("Đã duyệt patch.")).toBeInTheDocument();
  });

  it("calls reject and request-changes endpoints from packaging queue cards", async () => {
    const user = userEvent.setup();
    renderWithQuery("publish");

    await user.click(await screen.findByRole("button", { name: "Từ chối" }));
    await waitFor(() => {
      expect(apiMocks.rejectPackagingProposedPatch).toHaveBeenCalledWith("patch-1", "Từ chối từ hàng chờ duyệt.");
    });

    await user.click(await screen.findByRole("button", { name: "Yêu cầu chỉnh" }));
    await waitFor(() => {
      expect(apiMocks.requestChangesPackagingProposedPatch).toHaveBeenCalledWith("patch-1", "Yêu cầu chỉnh từ hàng chờ duyệt.");
    });
  });

  it("shows loading state while recording a packaging decision", async () => {
    let resolveApprove: (value: unknown) => void = () => {};
    apiMocks.approvePackagingProposedPatch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveApprove = resolve;
        })
    );
    const user = userEvent.setup();
    renderWithQuery();

    await user.click(await screen.findByRole("button", { name: "Duyệt" }));

    expect(screen.getByRole("button", { name: "Đang xử lý..." })).toBeDisabled();
    resolveApprove({ id: "decision-approve", decision: "APPROVE" });
    expect(await screen.findByText("Đã duyệt patch.")).toBeInTheDocument();
  });

  it("does not render provider, render, upload or publish job-control buttons", async () => {
    renderWithQuery();

    expect(await screen.findByText("Thiếu thumbnail brief (81c48d7a)")).toBeInTheDocument();
    [
      /Generate video/i,
      /^Render$/i,
      /Run provider/i,
      /Execute provider/i,
      /Upload YouTube/i,
      /^Publish$/i,
      /Auto publish/i,
      /Run daily/i,
      /Run vector/i,
      /Run NoView scanner/i
    ].forEach((name) => {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    });
  });
});

function packagingQueuePayload() {
  return {
    generated_at: "2026-07-06T00:00:00Z",
    summaries: [
      {
        queue_type: "packaging_review",
        label: "Packaging review",
        count: 1,
        priority: "NORMAL",
        next_action: "Duyệt proposed patch.",
        allowed_actions: ["APPROVE", "REJECT", "REQUEST_CHANGES"]
      }
    ],
    items: [
      {
        queue_item_id: "queue-1",
        queue_type: "packaging_review",
        entity_type: "packaging_review_queue_item",
        entity_id: "queue-1",
        channel: null,
        project: null,
        operator_summary: "Thiếu thumbnail brief (81c48d7a)",
        friendly_status: "ThumbnailTruthfulnessGate: PENDING_HUMAN_REVIEW",
        priority: "NORMAL",
        risk_level: "REVIEW_REQUIRED",
        confidence_label: "READY_FOR_REVIEW",
        freshness_label: "CURRENT",
        evidence_summary: "Chưa có concept/overlay/subject để human tạo thumbnail.",
        next_action: "Duyệt patch thumbnail brief.",
        due_at: null,
        allowed_actions: ["APPROVE", "REJECT", "REQUEST_CHANGES"],
        action_ref: {
          package_id: "pkg-1",
          queue_item_id: "queue-1",
          proposed_patch_id: "patch-1"
        },
        source_refs: [{ package_id: "pkg-1", proposed_patch_status: "READY_FOR_REVIEW" }],
        audit_refs: [],
        technical_appendix: {}
      }
    ]
  };
}
