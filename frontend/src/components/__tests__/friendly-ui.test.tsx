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

    expect(screen.getByText("Trung tâm điều hành")).toBeInTheDocument();
    expect(screen.getByText("Không gian kênh")).toBeInTheDocument();
    expect(screen.getByText("Video đã upload")).toBeInTheDocument();
    expect(screen.getByText("Gói publish")).toBeInTheDocument();
    expect(screen.queryByText("Command")).not.toBeInTheDocument();
    expect(screen.queryByText("Uploaded")).not.toBeInTheDocument();
  });

  it("does not show raw enum labels as the primary badge text", () => {
    render(<StatusBadge value="READY_FOR_HUMAN_REVIEW" />);

    expect(screen.getByText("Chờ người duyệt")).toBeInTheDocument();
    expect(screen.queryByText("READY_FOR_HUMAN_REVIEW")).not.toBeInTheDocument();
  });
});
