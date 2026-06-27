import { ExternalLink, HardDrive } from "lucide-react";

import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { GoogleDriveMedia } from "@/lib/types";

export function GoogleDriveMediaCard({ media }: { media: GoogleDriveMedia }) {
  return (
    <Panel className="min-h-44">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted text-primary">
            <HardDrive size={18} />
          </div>
          <div>
            <div className="text-sm font-semibold">{mediaTypeLabel(media.media_type)}</div>
            <div className="text-xs text-muted-foreground">Lưu trên Google Drive</div>
          </div>
        </div>
        <FriendlyStatusBadge value={media.status} />
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-muted-foreground">Xác minh</dt>
          <dd><FriendlyStatusBadge value={media.verification_status} /></dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Dọn local</dt>
          <dd><FriendlyStatusBadge value={media.cleanup_status} /></dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Dung lượng</dt>
          <dd>{media.file_size ? `${Math.round(media.file_size / 1024)} KB` : "Chưa có dữ liệu"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Đã upload</dt>
          <dd>{media.uploaded_at ? new Date(media.uploaded_at).toLocaleString("vi-VN") : "Chưa có dữ liệu"}</dd>
        </div>
      </dl>
      {media.friendly_error ? <p className="mt-3 text-sm text-amber-100">{media.friendly_error}</p> : null}
      <Button asChild className="mt-4 w-full" variant="primary">
        <a href={media.web_view_link} target="_blank" rel="noreferrer">
          <ExternalLink size={16} />
          Mở trên Google Drive
        </a>
      </Button>
    </Panel>
  );
}

function mediaTypeLabel(value: string) {
  return {
    LONG_FORM_FINAL: "Video dài hoàn chỉnh",
    SHORT_FINAL: "Video ngắn",
    THUMBNAIL: "Thumbnail",
    CAPTION_FILE: "Tệp phụ đề",
    SOURCE_ASSET: "Tệp nguồn"
  }[value.toUpperCase()] ?? "Tệp media";
}
