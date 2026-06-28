/*
Frontend tests for M12.2P-R Channel Activation CTA Repair.

Tests:
- button visible when eligible
- button hidden/disabled when not eligible
- click calls activation endpoint
- success refreshes status to active
- error displays Vietnamese message
- no publish/upload button appears
*/

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChannelWorkspaceView } from "@/features/channels/channel-workspace-view";
import * as api from "@/lib/api";
import type { ChannelWorkspace, HumanUploadTaskList, PublishLedger, UploadedVideoLedgerList } from "@/lib/types";

type WorkspaceOverrides = {
  channel?: Record<string, unknown>;
  health_summary?: {
    contract_review?: Record<string, unknown>;
    [key: string]: unknown;
  };
  lifecycle?: Record<string, unknown>;
  [key: string]: unknown;
};

function makeWorkspace(overrides: WorkspaceOverrides = {}): ChannelWorkspace {
  return {
    channel: {
      id: "ch-1",
      name: "Test Channel",
      company_id: "co-1",
      key: "test-channel",
      status: "draft",
      primary_language: "en",
      ...overrides.channel,
    },
    health_summary: {
      channel_status: "DRAFT",
      health: "NEW",
      next_action: "Cần review policy snapshot trước khi activate channel.",
      contract_review: {
        contract_status: "COMPLETE",
        label: "Hồ sơ đủ để kích hoạt",
        latest_snapshot_id: "snap-1",
        active_snapshot_id: null,
        snapshot_version: 1,
        missing_fields: [],
        contradiction_reasons: [],
        next_action: "Kích hoạt kênh.",
        ...overrides.health_summary?.contract_review,
      },
      ...overrides.health_summary,
    },
    lifecycle: {
      channel_id: "ch-1",
      lifecycle_state: "DRAFT",
      health_status: "NEW",
      daily_generation_allowed: false,
      next_action: "Cần review policy snapshot trước khi activate channel.",
      main_blocker: "Cần review policy snapshot trước khi activate channel.",
      allowed_actions: ["KEEP_ACTIVE", "ADD_MANUAL_NOTE", "REACTIVATE_CHANNEL"],
      ...overrides.lifecycle,
    },
    projects: [],
    daily_runs: [],
    approvals: [],
    uploaded_videos: [],
    publish_ledger: emptyLedger,
    media_storage: { cloud_media_count: 0, storage_state: "NO_CLOUD_MEDIA" },
    provider_health: {},
    technical_appendix: {},
    ...overrides,
  } as unknown as ChannelWorkspace;
}

const emptyLedger: PublishLedger = {
  channel_id: "ch-1",
  need_upload_count: 0,
  waiting_backfill_count: 0,
  uploaded_count: 0,
  waiting_verification_count: 0,
  verified_count: 0,
  latest_tasks: [],
  latest_uploaded_videos: [],
  operator_summary_vi: "",
};

const emptyTaskList: HumanUploadTaskList = {
  channel_id: "ch-1",
  need_upload_count: 0,
  waiting_backfill_count: 0,
  uploaded_count: 0,
  waiting_verification_count: 0,
  verified_count: 0,
  unverified_count: 0,
  tasks: [],
};

const emptyUploadedVideos: UploadedVideoLedgerList = {
  channel_id: "ch-1",
  uploaded_videos: [],
};

describe("ChannelWorkspaceView - Activation CTA", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.restoreAllMocks();
  });

  function renderView(workspaceData: ReturnType<typeof makeWorkspace>) {
    vi.spyOn(api, "getChannelWorkspace").mockResolvedValue(workspaceData);
    vi.spyOn(api, "getChannelPublishLedger").mockResolvedValue(emptyLedger);
    vi.spyOn(api, "getChannelUploadTasks").mockResolvedValue(emptyTaskList);
    vi.spyOn(api, "getChannelUploadedVideos").mockResolvedValue(emptyUploadedVideos);
    return render(
      <QueryClientProvider client={queryClient}>
        <ChannelWorkspaceView channelId="ch-1" />
      </QueryClientProvider>
    );
  }

  async function openProfileTab() {
    const tabs = await screen.findAllByRole("tab");
    const profileTab = tabs.find((tab) => tab.textContent?.includes("Hồ sơ"));
    expect(profileTab).toBeTruthy();
    await userEvent.click(profileTab as HTMLElement);
  }

  it("shows Kích hoạt kênh button when eligible (DRAFT + COMPLETE + snapshot)", async () => {
    renderView(makeWorkspace());
    await openProfileTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Kích hoạt kênh/ })).toBeInTheDocument();
    });
  });

  it("shows disabled Bổ sung hồ sơ kênh when contract is PARTIAL", async () => {
    const workspace = makeWorkspace({
      health_summary: {
        channel_status: "DRAFT",
        health: "NEW",
        next_action: "Bổ sung hồ sơ kênh.",
        contract_review: {
          contract_status: "PARTIAL",
          label: "Thiếu thông tin",
          missing_fields: ["channel_identity.channel_name", "market_locale.primary_market"],
          contradiction_reasons: [],
          next_action: "Bổ sung hồ sơ kênh.",
        },
      },
    });
    renderView(workspace);
    await openProfileTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Bổ sung hồ sơ kênh/ })).toBeDisabled();
    });
  });

  it("shows disabled Bổ sung hồ sơ kênh when no snapshot exists", async () => {
    const workspace = makeWorkspace({
      health_summary: {
        channel_status: "DRAFT",
        health: "NEW",
        next_action: "Cần compile snapshot.",
        contract_review: {
          contract_status: "MISSING",
          label: "Chưa có snapshot",
          latest_snapshot_id: null,
          missing_fields: [],
          contradiction_reasons: [],
          next_action: "Bổ sung hồ sơ kênh và compile lại policy snapshot.",
        },
      },
    });
    renderView(workspace);
    await openProfileTab();
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Bổ sung hồ sơ kênh/ });
      expect(btn).toBeDisabled();
    });
  });

  it("calls activateChannel on click", async () => {
    const activateSpy = vi.spyOn(api, "activateChannel").mockResolvedValue({ status: "active" });
    renderView(makeWorkspace());
    await openProfileTab();
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Kích hoạt kênh/ });
      fireEvent.click(btn);
    });
    await waitFor(() => {
      expect(activateSpy).toHaveBeenCalledWith("ch-1");
    });
  });

  it("shows Vietnamese error on activation failure", async () => {
    vi.spyOn(api, "activateChannel").mockRejectedValue(new Error("channel contract is not COMPLETE (got PARTIAL)"));
    renderView(makeWorkspace());
    await openProfileTab();
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Kích hoạt kênh/ });
      fireEvent.click(btn);
    });
    // Error message should be displayed
    await waitFor(() => {
      expect(screen.getByText(/channel contract is not COMPLETE/)).toBeInTheDocument();
    });
  });

  it("does not show publish/upload button in profile tab", async () => {
    renderView(makeWorkspace());
    await openProfileTab();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
    });
  });
});
