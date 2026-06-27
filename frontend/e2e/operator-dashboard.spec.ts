import { expect, test } from "@playwright/test";

const dashboardOrigin =
  process.env.VCOS_DASHBOARD_E2E_BASE_URL ?? `http://127.0.0.1:${process.env.VCOS_DASHBOARD_E2E_PORT ?? "3000"}`;

const corsHeaders = {
  "access-control-allow-origin": dashboardOrigin,
  "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type",
  "access-control-allow-methods": "GET,POST,OPTIONS"
};

test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    await route.fulfill({
      headers: corsHeaders,
      json: {
        authenticated: true,
        auth_enabled: true,
        auth_mode: "local_password",
        user: {
          id: "operator-e2e",
          email: "operator@local.vcos",
          role: "OWNER_ADMIN",
          display_name: "Operator"
        }
      }
    });
  });

  await page.route("**/dashboard/command-center", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    await route.fulfill({
      headers: corsHeaders,
      json: {
        generated_at: new Date().toISOString(),
        company_id: null,
        cards: [
          { key: "critical_queue", title: "Việc cần xử lý ngay", count: 2, severity: "HIGH", next_action: "Mở hàng chờ để xử lý.", route: "/queues" },
          { key: "learning_review", title: "Bài học chờ duyệt", count: 1, severity: "NORMAL", next_action: "Xem bằng chứng trước khi duyệt.", route: "/learning" }
        ],
        metrics: [{ key: "stale_metrics", label: "Metric YouTube cũ/chưa có", value: 1, state: "CHECK_FRESHNESS", next_action: "Sync analytics." }],
        required_actions: [],
        safety_warnings: [
          { key: "no_auto_publish", label: "Không tự publish", severity: "HARD_RULE", text: "Bảng điều hành không upload/publish/reupload tự động." },
          { key: "drive_cta_only", label: "Chỉ dùng CTA Google Drive", severity: "HARD_RULE", text: "Media chỉ mở qua Google Drive." }
        ],
        technical_appendix: { no_provider_calls: true }
      }
    });
  });
});

test("loads command center", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Trung tâm điều hành" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Không tự publish" })).toBeVisible();
  await expect(page.getByText("Bảng điều hành VCOS")).toBeVisible();
});
