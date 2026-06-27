import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/dashboard/command-center", async (route) => {
    await route.fulfill({
      json: {
        generated_at: new Date().toISOString(),
        company_id: null,
        cards: [
          { key: "critical_queue", title: "Critical Queue Count", count: 2, severity: "HIGH", next_action: "Review queues.", route: "/queues" },
          { key: "learning_review", title: "Learning Review Count", count: 1, severity: "NORMAL", next_action: "Review evidence.", route: "/learning" }
        ],
        metrics: [{ key: "stale_metrics", label: "YouTube Metrics Stale/Unknown", value: 1, state: "CHECK_FRESHNESS", next_action: "Sync analytics." }],
        required_actions: [],
        safety_warnings: [
          { key: "no_auto_publish", label: "No Auto Publish", severity: "HARD_RULE", text: "Dashboard does not auto publish." },
          { key: "drive_cta_only", label: "Drive CTA Only", severity: "HARD_RULE", text: "Drive web_view_link only." }
        ],
        technical_appendix: { no_provider_calls: true }
      }
    });
  });
});

test("loads command center", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Portfolio Command Center" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No Auto Publish" })).toBeVisible();
});
