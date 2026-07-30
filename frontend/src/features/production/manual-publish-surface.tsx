"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, FileVideo2, UploadCloud } from "lucide-react";

import { TechnicalAppendix } from "@/components/cockpit";
import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { safeReviewMediaUrl } from "@/lib/api";
import type {
  ManualPublish,
  ManualPublishConfirmationInput
} from "@/lib/types";
import { SafeTechnicalJson } from "./production-cockpit-card";

type PublishAction = "start" | "confirm" | "correct" | "verify";

const initialConfirmation: ManualPublishConfirmationInput = {
  platform_video_id: "",
  platform_video_url: "",
  actual_title: "",
  actual_description: "",
  actual_visibility: "PRIVATE",
  published_at: new Date(0).toISOString(),
  duration_seconds: 1,
  thumbnail_matches: false,
  captions_match: false,
  ai_disclosure_confirmed: false,
  rights_confirmed: false,
  accept_non_material_variance: false,
  playlist_id: "",
  operator_notes: ""
};

export function ManualPublishSurface({
  publish,
  onStart,
  onConfirm,
  onCorrect,
  onVerify,
  busyAction,
  expectedDurationSeconds
}: {
  publish: ManualPublish;
  onStart?: () => void;
  onConfirm?: (input: ManualPublishConfirmationInput) => void;
  onCorrect?: (input: ManualPublishConfirmationInput) => void;
  onVerify?: () => void;
  busyAction?: PublishAction | null;
  expectedDurationSeconds?: number;
}) {
  const [form, setForm] = useState<ManualPublishConfirmationInput>({
    ...initialConfirmation,
    platform_video_id: publish.platform_video_id ?? "",
    platform_video_url: publish.platform_video_url ?? "",
    actual_title: publish.actual_title ?? "",
    actual_description: publish.actual_description ?? "",
    actual_visibility: publish.actual_visibility ?? "PRIVATE",
    published_at: new Date().toISOString(),
    duration_seconds: expectedDurationSeconds ?? 1
  });
  const requiresCorrection =
    publish.mismatch_state === "MISMATCH" ||
    publish.correction_state === "CORRECTION_REQUIRED";
  const canSubmitConfirmation = ["IN_PROGRESS", "AWAITING_CONFIRMATION"].includes(
    publish.state
  );
  const confirmationState = publish.technical_appendix.confirmation_state;
  const canVerify =
    typeof confirmationState === "string" &&
    ["SUBMITTED", "VARIANCE_ACCEPTED"].includes(confirmationState) &&
    !publish.uploaded_video_id;
  const safeDriveUrl = safeHttpsUrl(publish.drive_web_view_url);
  const safeDownloadUrl = safeReviewMediaUrl(
    publish.verified_file_download_url
  );

  useEffect(() => {
    setForm((current) => ({
      ...current,
      platform_video_id: publish.platform_video_id ?? current.platform_video_id,
      platform_video_url: publish.platform_video_url ?? current.platform_video_url,
      actual_title: publish.actual_title ?? current.actual_title,
      actual_description: publish.actual_description ?? current.actual_description,
      actual_visibility: publish.actual_visibility ?? current.actual_visibility
    }));
  }, [
    publish.actual_description,
    publish.actual_title,
    publish.actual_visibility,
    publish.platform_video_id,
    publish.platform_video_url
  ]);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-primary">
            Upload thủ công
          </div>
          <h2 className="mt-1 text-xl font-semibold">Ghi nhận publish chính xác</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            Tải đúng file đã xác minh, upload bên ngoài VCOS, rồi quay lại nhập kết quả
            thực tế. VCOS không gọi API upload hoặc publish của nền tảng.
          </p>
        </div>
        <FriendlyStatusBadge value={publish.state} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel>
          <div className="flex items-center gap-2 text-sm font-semibold">
            <FileVideo2 className="text-primary" size={18} aria-hidden="true" />
            File đã review
          </div>
          <p className="mt-3 break-words text-sm font-medium">{publish.exact_file_name}</p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            Chỉ dùng đúng file này. Checksum đầy đủ nằm trong Phụ lục kỹ thuật bên dưới.
          </p>
          {safeDriveUrl ? (
            <Button className="mt-4" asChild>
              <a
                href={safeDriveUrl}
                rel="noreferrer"
                target="_blank"
              >
                Mở file trên Google Drive
                <ExternalLink size={16} aria-hidden="true" />
              </a>
            </Button>
          ) : safeDownloadUrl ? (
            <Button className="mt-4" asChild>
              <a download={publish.exact_file_name} href={safeDownloadUrl}>
                Tải file đã xác minh
                <ExternalLink size={16} aria-hidden="true" />
              </a>
            </Button>
          ) : (
            <p className="mt-4 text-xs text-amber-300">
              Chưa có file download hoặc liên kết Drive đã xác minh.
            </p>
          )}
        </Panel>
        <Panel>
          <div className="flex items-center gap-2 text-sm font-semibold">
            <UploadCloud className="text-primary" size={18} aria-hidden="true" />
            Đích publish
          </div>
          <p className="mt-3 text-sm font-medium">
            {[publish.destination_label, publish.destination_handle]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            Đối chiếu đúng kênh/handle trước khi bắt đầu upload bên ngoài VCOS.
          </p>
        </Panel>
        <Panel>
          <div className="flex items-center gap-2 text-sm font-semibold">
            <CheckCircle2 className="text-primary" size={18} aria-hidden="true" />
            Kết quả ghi nhận
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <FriendlyStatusBadge value={publish.mismatch_state} />
            <FriendlyStatusBadge value={publish.correction_state} />
            <FriendlyStatusBadge value={publish.uploaded_video_status} />
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            {publish.analytics_ready
              ? "Video đã đủ lineage để chuyển sang luồng phân tích."
              : "Chỉ sẵn sàng phân tích sau khi UploadedVideo được xác minh."}
          </p>
        </Panel>
      </div>

      {["READY", "READY_FOR_OPERATOR"].includes(publish.state) ? (
        <Panel className="border-primary/35">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-base font-semibold">Bắt đầu phiên upload thủ công</h3>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                Nút này chỉ ghi nhận rằng bạn bắt đầu thao tác ngoài VCOS; nó không gửi
                file lên YouTube hoặc nền tảng nào khác.
              </p>
            </div>
            <Button
              variant="primary"
              disabled={!onStart || Boolean(busyAction)}
              onClick={onStart}
            >
              {busyAction === "start"
                ? "Đang ghi nhận..."
                : "Bắt đầu upload thủ công"}
            </Button>
          </div>
        </Panel>
      ) : null}

      {canSubmitConfirmation || requiresCorrection ? (
        <Panel>
          <h3 className="text-base font-semibold">
            {requiresCorrection
              ? "Sửa xác nhận theo dữ liệu thực tế"
              : "Xác nhận kết quả upload"}
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Nhập đúng thông tin đang tồn tại trên nền tảng. VCOS sẽ đối chiếu và giữ lịch
            sử correction/variance, không ghi đè im lặng.
          </p>
          <form
            className="mt-5 grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (requiresCorrection) onCorrect?.(form);
              else onConfirm?.(form);
            }}
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Platform video ID">
                <input
                  aria-label="Platform video ID"
                  className={controlClass}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      platform_video_id: event.target.value
                    }))
                  }
                  required
                  value={form.platform_video_id}
                />
              </Field>
              <Field label="Platform video URL">
                <input
                  aria-label="Platform video URL"
                  className={controlClass}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      platform_video_url: event.target.value
                    }))
                  }
                  placeholder="https://..."
                  required
                  type="url"
                  value={form.platform_video_url}
                />
              </Field>
            </div>
            <Field label="Tiêu đề thực tế">
              <input
                aria-label="Tiêu đề thực tế"
                className={controlClass}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    actual_title: event.target.value
                  }))
                }
                required
                value={form.actual_title}
              />
            </Field>
            <Field label="Mô tả thực tế">
              <textarea
                aria-label="Mô tả thực tế"
                className={`${controlClass} min-h-28`}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    actual_description: event.target.value
                  }))
                }
                required
                value={form.actual_description}
              />
            </Field>
            <Field label="Visibility thực tế">
              <select
                aria-label="Visibility thực tế"
                className={controlClass}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    actual_visibility: event.target.value
                  }))
                }
                value={form.actual_visibility}
              >
                <option value="PRIVATE">Riêng tư</option>
                <option value="UNLISTED">Không công khai</option>
                <option value="PUBLIC">Công khai</option>
                <option value="SCHEDULED">Đã lên lịch</option>
              </select>
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Thời điểm publish thực tế">
                <input
                  aria-label="Thời điểm publish thực tế"
                  className={controlClass}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      published_at: new Date(event.target.value).toISOString()
                    }))
                  }
                  required
                  type="datetime-local"
                  value={toDateTimeLocal(form.published_at)}
                />
              </Field>
              <Field label="Thời lượng thực tế (giây)">
                <input
                  aria-label="Thời lượng thực tế (giây)"
                  className={controlClass}
                  min="0.001"
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      duration_seconds: Number(event.target.value)
                    }))
                  }
                  required
                  step="0.001"
                  type="number"
                  value={form.duration_seconds}
                />
              </Field>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <CheckField
                checked={form.thumbnail_matches}
                label="Thumbnail thực tế khớp bản đã review"
                onChange={(checked) =>
                  setForm((current) => ({ ...current, thumbnail_matches: checked }))
                }
              />
              <CheckField
                checked={form.captions_match}
                label="Phụ đề thực tế khớp bản đã review"
                onChange={(checked) =>
                  setForm((current) => ({ ...current, captions_match: checked }))
                }
              />
              <CheckField
                checked={form.ai_disclosure_confirmed}
                label="AI disclosure trên nền tảng khớp bản đã review"
                onChange={(checked) =>
                  setForm((current) => ({
                    ...current,
                    ai_disclosure_confirmed: checked
                  }))
                }
              />
              <CheckField
                checked={form.rights_confirmed}
                label="Đã đối chiếu quyền sử dụng theo bản review"
                onChange={(checked) =>
                  setForm((current) => ({ ...current, rights_confirmed: checked }))
                }
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Playlist ID (nếu có)">
                <input
                  aria-label="Playlist ID"
                  className={controlClass}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      playlist_id: event.target.value
                    }))
                  }
                  value={form.playlist_id ?? ""}
                />
              </Field>
              <Field label="Thứ tự trong playlist (nếu có)">
                <input
                  aria-label="Thứ tự trong playlist"
                  className={controlClass}
                  min="0"
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      playlist_order:
                        event.target.value === ""
                          ? undefined
                          : Number(event.target.value)
                    }))
                  }
                  type="number"
                  value={form.playlist_order ?? ""}
                />
              </Field>
            </div>
            <Field label="Ghi chú người vận hành (nếu có)">
              <textarea
                aria-label="Ghi chú người vận hành"
                className={`${controlClass} min-h-20`}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    operator_notes: event.target.value
                  }))
                }
                value={form.operator_notes ?? ""}
              />
            </Field>
            <CheckField
              checked={form.accept_non_material_variance}
              label="Tôi xác nhận variance không trọng yếu sau khi đối chiếu dữ liệu thực tế"
              onChange={(checked) =>
                setForm((current) => ({
                  ...current,
                  accept_non_material_variance: checked
                }))
              }
            />
            <div className="flex justify-end">
              <Button
                type="submit"
                variant="primary"
                disabled={
                  Boolean(busyAction) ||
                  (requiresCorrection ? !onCorrect : !onConfirm) ||
                  !form.platform_video_id ||
                  !form.platform_video_url
                }
              >
                {busyAction === "correct"
                  ? "Đang lưu correction..."
                  : busyAction === "confirm"
                    ? "Đang xác nhận..."
                    : requiresCorrection
                      ? "Lưu correction"
                      : "Xác nhận kết quả upload"}
              </Button>
            </div>
          </form>
        </Panel>
      ) : null}

      {canVerify ? (
        <Panel className="border-primary/35">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-base font-semibold">
                Xác minh dữ liệu đang thấy trên nền tảng
              </h3>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                Mở video bằng liên kết bên dưới, đối chiếu kênh, ID, tiêu đề,
                visibility, thời điểm và thời lượng. Nút này ghi bằng chứng quan
                sát gắn với phiên đăng nhập rồi mới tạo UploadedVideo.
              </p>
            </div>
            <Button
              disabled={!onVerify || Boolean(busyAction)}
              onClick={onVerify}
              variant="primary"
            >
              {busyAction === "verify"
                ? "Đang xác minh..."
                : "Đã đối chiếu, tạo UploadedVideo"}
            </Button>
          </div>
        </Panel>
      ) : null}

      {publish.platform_video_url || publish.platform_video_id ? (
        <Panel>
          <h3 className="text-base font-semibold">Kết quả trên nền tảng</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            {publish.actual_title ?? "Chưa ghi nhận tiêu đề thực tế"}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <FriendlyStatusBadge value={publish.actual_visibility} />
            <FriendlyStatusBadge
              value={publish.analytics_ready ? "ANALYTICS_READY" : "NOT_READY"}
            />
            {safeHttpsUrl(publish.platform_video_url) ? (
              <a
                className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
                href={safeHttpsUrl(publish.platform_video_url) ?? undefined}
                rel="noreferrer"
                target="_blank"
              >
                Mở video trên nền tảng
                <ExternalLink size={15} aria-hidden="true" />
              </a>
            ) : null}
          </div>
        </Panel>
      ) : null}

      <p className="text-sm font-medium">{publish.next_action}</p>

      <TechnicalAppendix>
        <SafeTechnicalJson
          value={{
            task_id: publish.task_id,
            project_id: publish.project_id,
            final_review_candidate_id: publish.final_review_candidate_id,
            reviewed_checksum_sha256: publish.reviewed_checksum_sha256,
            destination_channel_id: publish.destination_channel_id,
            platform_video_id: publish.platform_video_id,
            uploaded_video_id: publish.uploaded_video_id,
            ...publish.technical_appendix
          }}
        />
      </TechnicalAppendix>
    </section>
  );
}

const controlClass =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

function CheckField({
  checked,
  label,
  onChange
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex min-h-12 items-center gap-3 rounded-md border border-border bg-background/35 px-3 py-2 text-sm">
      <input
        checked={checked}
        className="h-4 w-4 accent-[hsl(var(--primary))]"
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span>{label}</span>
    </label>
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

function toDateTimeLocal(value: string) {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
