import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChannelInitWizard } from "@/features/channels/channel-init-wizard";

vi.mock("@/lib/api", () => ({
  initChannel: vi.fn(async () => ({ channel: { id: "channel-1" }, compiled: { id: "snapshot-1", contract_status: "COMPLETE" } })),
  activateChannel: vi.fn(async () => ({ contract_status: "COMPLETE" })),
  queryKeys: {
    channels: ["channels"]
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
  it("renders structured Channel Contract form without provider budget inputs", () => {
    renderWithQuery();

    expect(screen.getByRole("heading", { name: "Tạo kênh" })).toBeInTheDocument();
    expect(screen.getByText("Thông tin kênh")).toBeInTheDocument();
    expect(screen.getByText("Đối tượng người xem")).toBeInTheDocument();
    expect(screen.getByText("Thị trường & locale")).toBeInTheDocument();
    expect(screen.getByText("Editorial strategy")).toBeInTheDocument();
    expect(screen.getByText("Format policy")).toBeInTheDocument();
    expect(screen.getByText("Rights / disclosure policy")).toBeInTheDocument();
    expect(screen.getByText("Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.")).toBeInTheDocument();
    expect(screen.getAllByText("Thiếu thông tin cấu hình").length).toBeGreaterThan(0);
    expect(screen.queryByText("Ngân sách ký tự TTS")).not.toBeInTheDocument();
    expect(screen.queryByText("Ngân sách AI hero USD")).not.toBeInTheDocument();
    expect(screen.queryByText("Auto publish allowed", { selector: "label" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
  });
});
