import { Captions, CheckCircle2, ExternalLink, Film, ImageIcon, ShieldCheck } from "lucide-react";

import { TechnicalAppendix } from "@/components/cockpit";
import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { safeReviewMediaUrl } from "@/lib/api";
import type { FinalReview } from "@/lib/types";
import { SafeTechnicalJson } from "./production-cockpit-card";

type FinalDecision = "UPLOAD" | "DO_NOT_UPLOAD";

export function FinalReviewSurface({
  review,
  onDecision,
  busyDecision
}: {
  review: FinalReview;
  onDecision?: (decision: FinalDecision) => void;
  busyDecision?: FinalDecision | null;
}) {
  const context = review.series_title
    ? [review.series_title, review.run_label, review.episode_label].filter(Boolean).join(" · ")
    : review.standalone_reason ?? "Video độc lập theo kế hoạch nội dung";
  const safePlayerUrl = safeReviewMediaUrl(review.media.player_url);
  const safeThumbnailUrl = safeReviewMediaUrl(review.media.thumbnail_url);
  const safeDriveUrl = safeHttpsUrl(review.media.drive_web_view_url);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-primary">
            Video cuối
          </div>
          <h2 className="mt-1 text-xl font-semibold">Xem và quyết định</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            Đây là điểm quyết định đầu tiên của người vận hành trong luồng v2. Quyết định
            UPLOAD chỉ mở luồng upload thủ công; VCOS không gửi video lên nền tảng.
          </p>
        </div>
        <FriendlyStatusBadge value={review.state} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.45fr_0.55fr]">
        <Panel className="overflow-hidden p-0">
          {safePlayerUrl ? (
            <video
              aria-label="Video MP4 cuối để xem"
              className="aspect-video w-full bg-black"
              controls
              crossOrigin="use-credentials"
              playsInline
              poster={safeThumbnailUrl ?? undefined}
              preload="metadata"
              src={safePlayerUrl}
            >
              Trình duyệt không phát được video này. Hãy tải file đã xác minh từ buồng
              lái.
            </video>
          ) : (
            <div className="flex aspect-video items-center justify-center bg-black/70 p-6 text-center">
              <div>
                <Film className="mx-auto text-primary" size={34} aria-hidden="true" />
                <p className="mt-3 text-sm font-medium">MP4 cuối đã sẵn sàng để xem</p>
                <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
                  Backend chưa cung cấp URL phát đã xác minh cho archive hiện tại.
                </p>
              </div>
            </div>
          )}
          <div className="p-5">
            <h3 className="text-lg font-semibold">{review.title}</h3>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
              {review.description}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <FriendlyStatusBadge value={review.lane} />
              <FriendlyStatusBadge value={review.content_mode} />
              <FriendlyStatusBadge value={review.archive_status} />
            </div>
          </div>
        </Panel>

        <div className="grid content-start gap-4">
          <Panel>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <ImageIcon className="text-primary" size={18} aria-hidden="true" />
              Thumbnail
            </div>
            {safeThumbnailUrl ? (
              // A plain image is intentional: the URL is a runtime, archive-backed
              // review asset and is not known to Next's static image allow-list.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                alt={`Thumbnail cho ${review.title}`}
                className="mt-3 aspect-video w-full rounded-md border border-border object-cover"
                crossOrigin="use-credentials"
                src={safeThumbnailUrl}
              />
            ) : (
              <div className="mt-3 flex aspect-video items-center justify-center rounded-md border border-dashed border-border text-xs text-muted-foreground">
                Chưa có URL thumbnail an toàn
              </div>
            )}
          </Panel>
          <Panel>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Captions className="text-primary" size={18} aria-hidden="true" />
              Phụ đề và thời lượng
            </div>
            <p className="mt-3 text-sm">
              {review.media.captions_label ?? "Chưa có mô tả phụ đề"}
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              {formatDuration(review.media.duration_seconds)}
            </p>
          </Panel>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ReviewSummary
          icon={<ShieldCheck size={18} aria-hidden="true" />}
          title="Quyền sử dụng & disclosure"
          body={review.rights_disclosure_summary}
        />
        <ReviewSummary
          icon={<CheckCircle2 size={18} aria-hidden="true" />}
          title="Tự sửa trước khi xem"
          body={review.auto_repair_summary}
        />
        <ReviewSummary
          icon={<Film size={18} aria-hidden="true" />}
          title="Đích publish"
          body={[review.destination_label, review.destination_handle, context]
            .filter(Boolean)
            .join(" · ")}
        />
      </div>

      {review.audience_promise || review.strategic_intent ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <ReviewSummary
            icon={<ShieldCheck size={18} aria-hidden="true" />}
            title="Cam kết với khán giả"
            body={review.audience_promise ?? "Chưa có cam kết khán giả đã niêm phong."}
          />
          <ReviewSummary
            icon={<CheckCircle2 size={18} aria-hidden="true" />}
            title="Ý đồ chiến lược"
            body={formatStrategicIntent(review.strategic_intent)}
          />
        </div>
      ) : null}

      {review.warnings.length ? (
        <Panel className="border-amber-500/35 bg-amber-500/5">
          <h3 className="text-base font-semibold">Điểm cần lưu ý trước khi quyết định</h3>
          <ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
            {review.warnings.map((warning, index) => (
              <li key={`${warning}-${index}`} className="flex gap-2">
                <span aria-hidden="true" className="text-amber-300">
                  •
                </span>
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      <Panel className="border-primary/35">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold">Quyết định video cuối</h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              Chọn UPLOAD để chuẩn bị task upload thủ công, hoặc DO_NOT_UPLOAD để dừng
              video này. Không có nút duyệt package/gate ở đây.
            </p>
            {review.decision ? (
              <p className="mt-2 text-sm font-medium">
                Đã ghi quyết định: {review.decision}
                {review.decision_recorded_at
                  ? ` · ${formatDateTime(review.decision_recorded_at)}`
                  : ""}
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            {safeDriveUrl ? (
              <Button asChild>
                <a href={safeDriveUrl} rel="noreferrer" target="_blank">
                  Mở file trên Google Drive
                  <ExternalLink size={16} aria-hidden="true" />
                </a>
              </Button>
            ) : null}
            <Button
              variant="primary"
              disabled={Boolean(review.decision) || !onDecision || Boolean(busyDecision)}
              onClick={() => onDecision?.("UPLOAD")}
            >
              {busyDecision === "UPLOAD" ? "ĐANG GHI..." : "UPLOAD"}
            </Button>
            <Button
              variant="danger"
              disabled={Boolean(review.decision) || !onDecision || Boolean(busyDecision)}
              onClick={() => onDecision?.("DO_NOT_UPLOAD")}
            >
              {busyDecision === "DO_NOT_UPLOAD" ? "ĐANG GHI..." : "DO_NOT_UPLOAD"}
            </Button>
          </div>
        </div>
      </Panel>

      <TechnicalAppendix>
        <SafeTechnicalJson
          value={{
            candidate_id: review.candidate_id,
            project_id: review.project_id,
            workflow_run_id: review.workflow_run_id,
            checksum_sha256: review.media.checksum_sha256,
            file_name: review.media.file_name,
            lane: review.lane,
            content_mode: review.content_mode,
            ...review.technical_appendix
          }}
        />
      </TechnicalAppendix>
    </section>
  );
}

function ReviewSummary({
  icon,
  title,
  body
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <Panel>
      <div className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-primary">{icon}</span>
        {title}
      </div>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{body}</p>
    </Panel>
  );
}

function safeHttpsUrl(value?: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function formatDuration(value: number) {
  const totalSeconds = Math.max(0, Math.round(value));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")} phút`;
}

function formatStrategicIntent(value?: string | null) {
  const labels: Record<string, string> = {
    ACQUISITION: "Thu hút đúng khán giả mục tiêu",
    AUDIENCE_DEPTH: "Đào sâu mức độ gắn kết của khán giả",
    AUTHORITY: "Xây dựng uy tín và năng lực chuyên môn",
    SERIES_CONTINUITY: "Duy trì mạch nội dung của series",
    CONTROLLED_EXPERIMENT: "Thử nghiệm có kiểm soát"
  };
  return value ? labels[value] ?? value : "Chưa có ý đồ chiến lược đã niêm phong.";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(new Date(value));
}
