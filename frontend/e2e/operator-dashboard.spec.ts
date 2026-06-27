import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/auth/me", async (route) => {
    await route.fulfill({
      json: {
        authenticated: true,
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
    await route.fulfill({
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
