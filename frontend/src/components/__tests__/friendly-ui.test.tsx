import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn() })
}));

vi.mock("@/lib/auth", () => ({
  useCurrentUser: () => ({
    isLoading: false,
    isAuthenticated: true,
    session: {
      user: {
        email: "admin@local.vcos",
        role: "OWNER_ADMIN"
      }
    },
    logout: vi.fn()
  })
}));

describe("friendly Vietnamese dashboard UI", () => {
  it("shows Vietnamese sidebar labels", () => {
    render(<AppShell><main>Trang thử</main></AppShell>);

    expect(screen.getAllByText("Trung tâm").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Kênh").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Video đã upload").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Gói publish").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tệp Drive").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Vận hành").length).toBeGreaterThan(0);
    expect(screen.queryByText("Command")).not.toBeInTheDocument();
    expect(screen.queryByText("Uploaded")).not.toBeInTheDocument();
    expect(screen.queryByText("Không gian kênh")).not.toBeInTheDocument();
  });

  it("does not show raw enum labels as the primary badge text", () => {
    render(<StatusBadge value="READY_FOR_HUMAN_REVIEW" />);

    expect(screen.getByText("Chờ người duyệt")).toBeInTheDocument();
    expect(screen.queryByText("READY_FOR_HUMAN_REVIEW")).not.toBeInTheDocument();
  });

  it("uses a safe Vietnamese fallback for unmapped backend enum labels", () => {
    render(<StatusBadge value="SOME_NEW_BACKEND_ENUM" />);

    expect(screen.getByText("Chưa có dữ liệu")).toBeInTheDocument();
    expect(screen.queryByText("SOME_NEW_BACKEND_ENUM")).not.toBeInTheDocument();
  });
});
