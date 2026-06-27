import { ExternalLink, HardDrive } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
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
            <div className="text-sm font-semibold">{media.media_type.replaceAll("_", " ").toLowerCase()}</div>
            <div className="text-xs text-muted-foreground">Lưu trên Google Drive</div>
          </div>
        </div>
        <StatusBadge value={media.status} />
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-muted-foreground">Xác minh</dt>
          <dd><StatusBadge value={media.verification_status} /></dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Dọn local</dt>
          <dd><StatusBadge value={media.cleanup_status} /></dd>
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
          {media.cta_label}
        </a>
      </Button>
    </Panel>
  );
}
