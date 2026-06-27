import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadedVideosView } from "@/features/uploaded-videos/uploaded-videos-view";

vi.mock("@/lib/api", () => ({
  getUploadedVideos: vi.fn(async () => []),
  queryKeys: {
    uploadedVideos: ["uploaded-videos"]
  }
}));

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UploadedVideosView />
    </QueryClientProvider>
  );
}

describe("UploadedVideosView", () => {
  it("shows a helpful Vietnamese empty state with next actions and summary cards", async () => {
    renderWithQuery();

    expect(await screen.findByText("Chưa có video đã upload")).toBeInTheDocument();
    expect(screen.getByText(/Sau khi bạn publish video thủ công lên YouTube/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Đi tới gói publish" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Xem hướng dẫn paste-back" })).toBeInTheDocument();
    expect(screen.getByText("Tổng video đã upload")).toBeInTheDocument();
    expect(screen.getByText("Cần xác nhận publish")).toBeInTheDocument();
    expect(screen.getByText("Analytics cần sync")).toBeInTheDocument();
    expect(screen.getByText("Video cần recovery")).toBeInTheDocument();
    expect(screen.queryByText("No uploaded videos")).not.toBeInTheDocument();
    expect(screen.queryByText("Uploaded Videos")).not.toBeInTheDocument();
  });
});
