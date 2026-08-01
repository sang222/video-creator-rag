import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LaunchCadenceDashboardView } from "@/features/launch/launch-cadence-dashboard";
import * as api from "@/lib/api";
import type { LaunchCadenceDashboard } from "@/lib/types";

const dashboard: LaunchCadenceDashboard = {
  generated_at: "2026-07-30T08:00:00Z",
  channel_id: "channel-1",
  channel_name: "Evidence Lab",
  launch_mode: "CONTROLLED_EVIDENCE_BUILDING",
  launch_day: 5,
  launch_state: "ACTIVE",
  launch_run_id: "launch-1",
  policy_version_id: "policy-1",
  policy_hash: "hash-1",
  runway: {
    idea_candidates: 12,
    preflight_passed: 8,
    greenlit: 6,
    in_production: 1,
    final_review_ready: 2,
    upload_approved: 1,
    published: 3,
    rejected_or_expired: 1,
    targets: {
      idea_candidates: 12,
      preflight_passed: 8,
      greenlit: 6,
      public_ready_buffer: 3
    }
  },
  public_ready_buffer: {
    count: 2,
    target: 3,
    state: "BELOW_TARGET"
  },
  active_series: [
    {
      series_plan_id: "series-plan-1",
      series_run_id: "series-run-1",
      display_name: "Operator Evidence",
      state: "ACTIVE",
      next_episode_number: 4
    }
  ],
  videos_published: 3,
  next_publish_slot: {
    slot_id: "slot-1",
    publish_at: "2026-08-01T13:00:00Z",
    timezone: "Asia/Ho_Chi_Minh",
    weekday: "SATURDAY",
    state: "PLANNED"
  },
  next_production_start_window: {
    opens_at: "2026-07-31T01:00:00Z",
    closes_at: "2026-07-31T05:00:00Z",
    timezone: "Asia/Ho_Chi_Minh"
  },
  current_experiment: {
    public_video_number: 3,
    phase: "AUDIENCE_PROMISE_EVIDENCE",
    primary_variable: "Audience promise",
    baseline_refs: [],
    comparison_group: "launch-1"
  },
  latest_evaluation: {
    evaluation_id: "evaluation-1",
    evaluated_at: "2026-07-30T07:55:00Z",
    decision: "WAIT_ACTIVE_PRODUCTION",
    reason_codes: ["ACTIVE_PRODUCTION_LIMIT_REACHED"],
    buffer_count: 2,
    active_production_count: 1,
    eligible_candidate_count: 2,
    eligible_publish_slot: null,
    input_hash: "input-hash",
    decision_hash: "decision-hash"
  },
  blockers: [],
  next_action: "Chờ lượt sản xuất hiện tại hoàn tất.",
  phase_e_analytics: {
    state: "PHASE_E_NOT_AVAILABLE",
    subscriber_count: null,
    valid_public_watch_hours_12m: null,
    projected_full_ypp_date: null
  },
  permissions: {
    can_pause: true,
    can_resume: false,
    can_evaluate: true
  },
  safety_notice: "Không tự publish; final decision vẫn do người vận hành.",
  technical_appendix: {}
};

describe("LaunchCadenceDashboardView", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false }
      }
    });
    vi.restoreAllMocks();
    vi.spyOn(api, "getLaunchCadenceDashboard").mockResolvedValue(dashboard);
  });

  function renderDashboard() {
    return render(
      <QueryClientProvider client={queryClient}>
        <LaunchCadenceDashboardView channelId="channel-1" />
      </QueryClientProvider>
    );
  }

  it("shows runway, buffer, slot, experiment, reason codes and post-upload analytics fallback", async () => {
    renderDashboard();

    expect(await screen.findByText("Evidence Lab")).toBeInTheDocument();
    expect(screen.getByText("12/12")).toBeInTheDocument();
    expect(screen.getByText("2/3")).toBeInTheDocument();
    expect(screen.getByText("Operator Evidence")).toBeInTheDocument();
    expect(screen.getByText("Audience promise")).toBeInTheDocument();
    expect(
      screen.getByText("ACTIVE_PRODUCTION_LIMIT_REACHED")
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Học sau upload long-form")
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Chưa có video long-form đã xác nhận upload để lập các mốc 24 giờ, 72 giờ, 7 ngày và 30 ngày."
      )
    ).toBeInTheDocument();
  });

  it("only sends channel-scoped pause and evaluate actions", async () => {
    const pauseSpy = vi
      .spyOn(api, "pauseLaunchCadence")
      .mockResolvedValue({ ...dashboard, launch_state: "PAUSED" });
    const evaluateSpy = vi
      .spyOn(api, "evaluateCadence")
      .mockResolvedValue(dashboard.latest_evaluation!);
    renderDashboard();

    await userEvent.click(
      await screen.findByRole("button", { name: "Tạm dừng cadence" })
    );
    await waitFor(() => {
      expect(pauseSpy).toHaveBeenCalledWith("channel-1");
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Đánh giá cadence" })
    );
    await waitFor(() => {
      expect(evaluateSpy).toHaveBeenCalledWith("channel-1");
    });

    expect(
      screen.queryByRole("button", { name: /bắt đầu sản xuất/i })
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/actor/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/format/i)).not.toBeInTheDocument();
  });
});
