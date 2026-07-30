import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorPlanningLauncher } from "@/features/production/operator-planning-launcher";
import { prepareAndLaunchOperatorPlanningSource } from "@/lib/api";
import type { OperatorPlanningCatalog } from "@/lib/types";

const catalog: OperatorPlanningCatalog = {
  generated_at: "2026-07-30T00:00:00Z",
  daily_short_options: [
    {
      source_id: "daily-slot-raw-id",
      source_type: "DAILY_SLOT",
      lane: "DAILY_SHORT",
      title: "Ba bước kiểm tra nhanh",
      company_label: "VCOS Studio",
      channel_label: "Kênh vận hành",
      slot_label: "Short hằng ngày · 30/07/2026",
      assignment_label: "Video độc lập",
      duration_label: "Mục tiêu 45s · khoảng 30–60s",
      state: "READY",
      status_label: "Sẵn sàng chuẩn bị và tạo dự án",
      launchable: true,
      guidance:
        "Backend tạo proposal, strict preflight và support authority từ nguồn persisted.",
      technical_appendix: {
        support_authority_preparation_required: true
      }
    },
    {
      source_id: "blocked-source-raw-id",
      source_type: "DAILY_IDEA",
      lane: "DAILY_SHORT",
      title: "Ý tưởng chưa đủ bằng chứng",
      company_label: "VCOS Studio",
      channel_label: "Kênh vận hành",
      slot_label: "Short hằng ngày · 31/07/2026",
      assignment_label: "Video độc lập",
      state: "BLOCKED",
      status_label: "Chưa đủ điều kiện",
      launchable: false,
      guidance:
        "Kênh chưa có search-demand evidence persisted để backend chạy strict preflight.",
      technical_appendix: {
        reason_code: "SEARCH_DEMAND_EVIDENCE_MISSING"
      }
    }
  ],
  long_form_options: [
    {
      source_id: "long-slot-raw-id",
      source_type: "LONG_FORM_PLAN",
      lane: "LONG_FORM",
      title: "Video dài ngày 30/07/2026",
      company_label: "VCOS Studio",
      channel_label: "Kênh vận hành",
      slot_label: "Video dài · 30/07/2026",
      assignment_label: "Video độc lập",
      duration_label: "Mục tiêu 480s · khoảng 420–540s",
      state: "READY",
      status_label: "Sẵn sàng chuẩn bị và tạo dự án",
      launchable: true,
      guidance:
        "Backend tạo strict preflight và support authority từ nguồn persisted.",
      technical_appendix: {
        support_authority_preparation_required: true
      }
    }
  ],
  safety_notice:
    "VCOS đóng băng support authority qua LLMRouter, không gọi media provider và không tự publish/upload.",
  technical_appendix: {
    read_model_only: true
  }
};

vi.mock("@/lib/api", () => ({
  getOperatorPlanningCatalog: vi.fn(async () => catalog),
  prepareAndLaunchOperatorPlanningSource: vi.fn(
    async (input: {
      sourceType: "DAILY_SLOT" | "DAILY_IDEA" | "LONG_FORM_PLAN";
    }) => ({
      lane:
        input.sourceType === "LONG_FORM_PLAN" ? "LONG_FORM" : "DAILY_SHORT",
      title:
        input.sourceType === "LONG_FORM_PLAN"
          ? "Video dài ngày 30/07/2026"
          : "Ba bước kiểm tra nhanh",
      admission_id: "admission-1",
      project_id:
        input.sourceType === "LONG_FORM_PLAN" ? "project-2" : "project-1",
      workflow_run_id: "workflow-1",
      workflow_state: "PLANNING_PENDING",
      reused_admission: false,
      reused_workflow: false,
      next_action: "Mở dự án để theo dõi.",
      technical_appendix: {
        media_provider_calls: false,
        automatic_publish: false
      }
    })
  ),
  queryKeys: {
    operatorPlanning: ["operator-planning"],
    productionCockpit: (projectId?: string) => [
      "production-cockpit",
      projectId ?? "next"
    ],
    commandCenter: ["command-center"]
  }
}));

function renderLauncher() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  });
  return render(
    <QueryClientProvider client={client}>
      <OperatorPlanningLauncher />
    </QueryClientProvider>
  );
}

describe("OperatorPlanningLauncher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts a Daily slot through the unified ID-only action", async () => {
    renderLauncher();
    const user = userEvent.setup();

    expect(
      await screen.findByText("Chuẩn bị và khởi động từ authority đã có")
    ).toBeInTheDocument();
    expect(screen.getByText("Ý tưởng chưa đủ bằng chứng")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Kênh chưa có search-demand evidence persisted để backend chạy strict preflight."
      )
    ).toBeInTheDocument();
    expect(screen.queryByText("daily-slot-raw-id")).not.toBeInTheDocument();
    expect(
      screen.queryByText("SEARCH_DEMAND_EVIDENCE_MISSING")
    ).not.toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("Chọn nguồn Short hằng ngày"),
      "daily-slot-raw-id"
    );
    await user.click(
      screen.getByRole("button", {
        name: "Chuẩn bị và bắt đầu Short"
      })
    );

    expect(prepareAndLaunchOperatorPlanningSource).toHaveBeenCalledWith({
      sourceType: "DAILY_SLOT",
      sourceId: "daily-slot-raw-id",
      maxBudgetUsd: 0
    });
    expect(
      await screen.findByRole("link", { name: "Mở tiến độ sản xuất" })
    ).toHaveAttribute("href", "/projects/project-1/production");
    expect(
      screen.queryByRole("button", { name: /tự publish/i })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upload tự động/i })
    ).not.toBeInTheDocument();
  });

  it("submits only the selected Long-form source ID and budget", async () => {
    renderLauncher();
    const user = userEvent.setup();

    await screen.findByText("Chuẩn bị và khởi động từ authority đã có");
    await user.selectOptions(
      screen.getByLabelText("Chọn nguồn Video dài"),
      "long-slot-raw-id"
    );
    expect(screen.queryByLabelText("Tiêu đề video dài")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Idea market preflight")
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "Chuẩn bị và bắt đầu video dài"
      })
    );

    expect(prepareAndLaunchOperatorPlanningSource).toHaveBeenCalledWith({
      sourceType: "LONG_FORM_PLAN",
      sourceId: "long-slot-raw-id",
      maxBudgetUsd: 0
    });
  });

});
