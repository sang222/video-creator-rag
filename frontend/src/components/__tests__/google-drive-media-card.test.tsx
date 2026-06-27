import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GoogleDriveMediaCard } from "@/components/google-drive-media-card";

describe("GoogleDriveMediaCard", () => {
  it("shows Drive CTA without exposing local paths or proxy links", () => {
    const { container } = render(
      <GoogleDriveMediaCard
        media={{
          id: "cloud-1",
          storage: "Google Drive",
          media_type: "LONG_FORM_FINAL",
          status: "VERIFIED",
          cta_label: "Mở trên Google Drive",
          web_view_link: "https://drive.google.com/file/d/cloud-1/view",
          file_size: 2048,
          uploaded_at: "2026-06-27T00:00:00Z",
          cleanup_status: "CLEANED",
          verification_status: "SIZE_VERIFIED",
          technical_appendix: { no_local_path: true, no_backend_download: true }
        }}
      />
    );

    const link = screen.getByRole("link", { name: /mở trên google drive/i });
    expect(link).toHaveAttribute("href", "https://drive.google.com/file/d/cloud-1/view");
    expect(container.textContent).not.toContain("/Users/");
    expect(container.innerHTML).not.toContain("web_content_link");
    expect(container.innerHTML).not.toContain("preview_url");
  });
});
