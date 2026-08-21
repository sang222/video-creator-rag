import { AlertTriangle, ExternalLink, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { YouTubePrivateStage } from "@/lib/types";

export function PrivatePublicationSurface({
  stage,
  busy,
  onReview
}: {
  stage: YouTubePrivateStage;
  busy?: "REJECT" | "NEEDS_RERENDER" | null;
  onReview: (disposition: "REJECT" | "NEEDS_RERENDER", reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const reviewable = stage.state === "PRIVATE_VERIFIED";
  const isPublic = stage.state === "PUBLICATION_VERIFIED";
  const isRework = stage.state === "REJECTED" || stage.state === "NEEDS_RERENDER";
  return (
    <Panel className="space-y-4 border-primary/35">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 text-primary" size={20} aria-hidden="true" />
        <div>
          <h2 className="text-lg font-semibold">
            {isPublic
              ? "YouTube PUBLIC đã được VCOS quan sát"
              : isRework
                ? "Stage PRIVATE đã chuyển sang rework"
                : "YouTube PRIVATE đã sẵn sàng để review"}
          </h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {isPublic
              ? "VCOS đã đối chiếu đúng video và channel ở trạng thái PUBLIC. Analytics và các downstream publication effects được mở sau mốc này."
              : isRework
                ? "Stage cũ vẫn giữ nguyên làm bằng chứng lịch sử. Replacement phải đi qua package/version lineage mới; VCOS không xoá video PRIVATE từ xa."
                : "Mở đúng asset đã stage trong YouTube Studio, xem toàn bộ package thực tế, rồi tự bấm PUBLIC. VCOS chỉ quan sát kết quả; VCOS không publish."}
          </p>
        </div>
      </div>

      <div className="rounded-md border border-amber-500/35 bg-amber-500/5 p-4 text-sm leading-6">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 text-amber-300" size={17} aria-hidden="true" />
          <span>
            Thumbnail và caption đã được đối chiếu với effect/hash cục bộ. Byte remote
            chính xác không thể được VCOS khẳng định; việc xem full-watch của con người
            là thủ tục/attestation, không phải machine fact.
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {stage.studio_url ? (
          <Button asChild>
            <a href={safeStudioUrl(stage.studio_url) ?? "#"} rel="noreferrer" target="_blank">
              Mở YouTube Studio
              <ExternalLink size={16} aria-hidden="true" />
            </a>
          </Button>
        ) : null}
        {reviewable ? (
          <>
            <Button
              variant="secondary"
              disabled={Boolean(busy) || !reason.trim()}
              onClick={() => onReview("NEEDS_RERENDER", reason)}
            >
              {busy === "NEEDS_RERENDER" ? "ĐANG GHI..." : "Cần render lại"}
            </Button>
            <Button
              variant="danger"
              disabled={Boolean(busy) || !reason.trim()}
              onClick={() => onReview("REJECT", reason)}
            >
              {busy === "REJECT" ? "ĐANG GHI..." : "Từ chối stage"}
            </Button>
          </>
        ) : null}
      </div>

      <div className="grid gap-3 text-sm md:grid-cols-2">
        <div>
          <span className="text-muted-foreground">Title đã stage:</span>{" "}
          <span>{stage.staged_title ?? "Chưa có"}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Assurance thumbnail/caption:</span>{" "}
          <span>
            {stage.thumbnail_assurance ?? "—"} / {stage.caption_assurance ?? "—"}
          </span>
        </div>
      </div>

      {reviewable ? (
        <label className="block text-sm">
          <span className="font-medium">Lý do reject / render lại (nếu cần)</span>
          <textarea
            className="mt-2 min-h-20 w-full rounded-md border border-border bg-background p-3 text-sm"
            maxLength={4000}
            placeholder="Ghi ngắn gọn điều cần xử lý…"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Stage: {stage.state} · Video ID: {stage.platform_video_id ?? "chưa có"}
        {stage.last_error_code ? ` · ${stage.last_error_code}` : ""}
      </p>
      <p className="break-all text-xs text-muted-foreground">
        Package checksum: {stage.final_media_checksum} · Expectation hash: {stage.public_release_expectation_hash}
      </p>
    </Panel>
  );
}

function safeStudioUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "studio.youtube.com"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}
