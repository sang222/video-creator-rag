import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChannelInitWizard } from "@/features/channels/channel-init-wizard";

const apiMocks = vi.hoisted(() => ({
  activateChannel: vi.fn(),
  createCompany: vi.fn(),
  getCompanies: vi.fn(),
  initChannel: vi.fn()
}));

vi.mock("@/lib/api", () => ({
  activateChannel: apiMocks.activateChannel,
  createCompany: apiMocks.createCompany,
  getCompanies: apiMocks.getCompanies,
  initChannel: apiMocks.initChannel,
  queryKeys: {
    channels: ["channels"],
    companies: ["companies"]
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
  beforeEach(() => {
    apiMocks.activateChannel.mockResolvedValue({ contract_status: "COMPLETE" });
    apiMocks.createCompany.mockResolvedValue({ id: "11111111-1111-4111-8111-111111111111", name: "VCOS Company", slug: "vcos-company", description: "", status: "active", default_currency: "USD" });
    apiMocks.getCompanies.mockResolvedValue([{ id: "11111111-1111-4111-8111-111111111111", name: "VCOS Company", slug: "vcos-company", description: "", status: "active", default_currency: "USD" }]);
    apiMocks.initChannel.mockResolvedValue({ channel: { id: "channel-1" }, compiled: { id: "snapshot-1", contract_status: "COMPLETE" } });
  });

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

  it("shows create-company CTA when no companies exist", async () => {
    apiMocks.getCompanies.mockResolvedValue([]);

    renderWithQuery();

    expect(await screen.findByText("Tạo công ty trước")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo công ty" })).toBeInTheDocument();
    expect(screen.queryByLabelText("ID công ty *")).not.toBeInTheDocument();
  });

  it("uses company dropdown instead of raw UUID input", async () => {
    renderWithQuery();

    const select = await screen.findByLabelText("Công ty *");

    expect(select.tagName).toBe("SELECT");
    await waitFor(() => {
      expect(screen.getByText("VCOS Company (vcos-company)")).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("ID công ty *")).not.toBeInTheDocument();
  });

  it("auto-selects one existing company", async () => {
    renderWithQuery();

    await waitFor(() => {
      expect(screen.getByLabelText("Công ty *")).toHaveValue("11111111-1111-4111-8111-111111111111");
    });
  });

  it("submits selected company UUID in channel init payload", async () => {
    const user = userEvent.setup();
    renderWithQuery();

    await fillRequiredChannelFields(user);
    await user.click(screen.getByRole("button", { name: /Tạo và compile snapshot/i }));

    await waitFor(() => expect(apiMocks.initChannel).toHaveBeenCalled());
    expect(apiMocks.initChannel.mock.calls[0][0]).toMatchObject({
      company_id: "11111111-1111-4111-8111-111111111111",
      key: "vcos-channel",
      name: "VCOS Channel"
    });
  });
});

async function fillRequiredChannelFields(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getByLabelText("Công ty *")).toHaveValue("11111111-1111-4111-8111-111111111111");
  });
  await user.type(screen.getByLabelText("Khóa kênh *"), "vcos-channel");
  await user.type(screen.getByLabelText("Tên kênh *"), "VCOS Channel");
  await user.type(screen.getByLabelText("Niche / chủ đề chính *"), "AI video operations");
  await user.type(screen.getByLabelText("Định vị kênh *"), "Practical operator guidance");
  await user.type(screen.getByLabelText("Brand promise / lời hứa nội dung *"), "Clear safe automation lessons");
  await user.type(screen.getByLabelText("Persona chính *"), "Operators");
  await user.type(screen.getByLabelText("Pain points *"), "Unsafe automation, unclear handoffs");
  await user.type(screen.getByLabelText("Desired outcome *"), "Run safe video workflows");
  await user.selectOptions(screen.getByLabelText("Primary market *"), "US");
  await user.selectOptions(screen.getByLabelText("Audience locale *"), "en-US");
  await user.type(screen.getByLabelText("Content language *"), "en");
  await user.type(screen.getByLabelText("Timezone *"), "America/Los_Angeles");
  await user.type(screen.getByLabelText("Currency *"), "USD");
  await user.type(screen.getByLabelText("Tone văn hóa *"), "clear");
  await user.type(screen.getByLabelText("Formality *"), "professional");
  await user.type(screen.getByLabelText("Humor *"), "light");
  await user.type(screen.getByLabelText("CTA style *"), "practical");
  await user.type(screen.getByLabelText("Finance claim sensitivity *"), "high");
  await user.type(screen.getByLabelText("Health claim sensitivity *"), "high");
  await user.type(screen.getByLabelText("Disclosure standard *"), "explicit");
  await user.type(screen.getByLabelText("Content pillars *"), "Safety\nOperations");
  await user.type(screen.getByLabelText("Allowed angles *"), "Operator walkthroughs");
  await user.type(screen.getByLabelText("Forbidden angles *"), "Provider bypasses");
  await user.type(screen.getByLabelText("Allowed topics *"), "Runtime contracts");
  await user.type(screen.getByLabelText("Forbidden topics *"), "Fake engagement");
  await user.type(screen.getByLabelText("Allowed style *"), "Calm explanations");
  await user.type(screen.getByLabelText("Min evidence required *"), "operator notes");
}
