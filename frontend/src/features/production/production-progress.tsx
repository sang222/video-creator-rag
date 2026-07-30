import { Clock3, Cpu, HardDrive, RotateCcw, ShieldAlert, WalletCards } from "lucide-react";

import { TechnicalAppendix } from "@/components/cockpit";
import {
  FriendlyStatusBadge,
  friendlyStatusLabel
} from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { ProductionProgress } from "@/lib/types";
import { SafeTechnicalJson } from "./production-cockpit-card";

type ProgressAction = "start" | "resume" | "cancel";

export function ProductionProgressSurface({
  progress,
  onAction,
  busyAction
}: {
  progress: ProductionProgress;
  onAction?: (action: ProgressAction) => void;
  busyAction?: ProgressAction | null;
}) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Tiến độ sản xuất</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Theo dõi worker, thử lại, chi phí, render, kiểm tra chất lượng và lưu trữ
            trong một luồng duy nhất.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {progress.operator_action === "START_PRODUCTION" ? (
            <Button
              variant="primary"
              disabled={!onAction || Boolean(busyAction)}
              onClick={() => onAction?.("start")}
            >
              {busyAction === "start" ? "Đang bắt đầu..." : "Bắt đầu sản xuất"}
            </Button>
          ) : null}
          {progress.operator_action === "RESUME_PRODUCTION" ? (
            <Button
              variant="primary"
              disabled={!onAction || Boolean(busyAction)}
              onClick={() => onAction?.("resume")}
            >
              <RotateCcw size={16} aria-hidden="true" />
              {busyAction === "resume" ? "Đang tiếp tục..." : "Tiếp tục an toàn"}
            </Button>
          ) : null}
          {!["COMPLETED", "FAILED", "CANCELLED"].includes(progress.state) ? (
            <Button
              variant="danger"
              disabled={!onAction || Boolean(busyAction)}
              onClick={() => onAction?.("cancel")}
            >
              {busyAction === "cancel" ? "Đang yêu cầu dừng..." : "Dừng sản xuất"}
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <ProgressMetric
          icon={<Cpu size={17} aria-hidden="true" />}
          label="Luồng công việc"
          value={progress.state}
          hint={
            progress.active_stage
              ? `Đang ở ${friendlyStageLabel(progress.active_stage)}`
              : "Chưa có bước đang chạy"
          }
        />
        <ProgressMetric
          icon={<Clock3 size={17} aria-hidden="true" />}
          label="Worker và thử lại"
          value={progress.lease_health}
          hint={
            progress.next_retry_at
              ? `Lần thử tiếp theo ${formatDateTime(progress.next_retry_at)}`
              : `${progress.retry_count} lần thử lại`
          }
        />
        <ProgressMetric
          icon={<WalletCards size={17} aria-hidden="true" />}
          label="Ngân sách"
          value={progress.budget_status}
          hint={budgetHint(progress)}
        />
        <ProgressMetric
          icon={<HardDrive size={17} aria-hidden="true" />}
          label="Lưu trữ"
          value={progress.archive_status}
          hint="Chỉ hoàn tất khi checksum và readback đã xác minh."
        />
      </div>

      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">Các bước trong luồng</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Thời điểm và số lần thử được lấy từ điều phối bền vững.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <FriendlyStatusBadge value={progress.provider_status} />
            <FriendlyStatusBadge value={progress.render_status} />
            <FriendlyStatusBadge value={progress.qc_status} />
          </div>
        </div>

        {progress.stages.length ? (
          <ol className="mt-5 grid gap-3">
            {progress.stages.map((stage, index) => (
              <li
                key={`${stage.stage}-${index}`}
                className="grid gap-3 rounded-md border border-border bg-background/30 p-3 md:grid-cols-[2rem_1fr_auto]"
              >
                <div className="flex h-8 w-8 items-center justify-center rounded-full border border-primary/40 bg-primary/10 text-sm font-semibold text-primary">
                  {index + 1}
                </div>
                <div>
                  <div className="text-sm font-medium">{friendlyStageLabel(stage.stage)}</div>
                  <div className="mt-1 text-xs leading-5 text-muted-foreground">
                    {stage.summary ?? stageTimeSummary(stage.started_at, stage.finished_at)}
                  </div>
                  {stage.retry_count ? (
                    <div className="mt-1 text-xs text-amber-300">
                      Đã thử lại {stage.retry_count} lần
                      {stage.next_retry_at
                        ? ` · tiếp tục ${formatDateTime(stage.next_retry_at)}`
                        : ""}
                    </div>
                  ) : null}
                </div>
                <FriendlyStatusBadge value={stage.state} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-5 rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
            Chưa có bước nào được ghi nhận. Khi luồng bắt đầu, tiến độ sẽ xuất hiện tại
            đây.
          </p>
        )}
      </Panel>

      <Panel
        className={
          progress.blocking_incident
            ? "border-destructive/50 bg-destructive/5"
            : "border-primary/25"
        }
      >
        <div className="flex items-start gap-3">
          <ShieldAlert
            aria-hidden="true"
            className={progress.blocking_incident ? "text-destructive" : "text-primary"}
            size={20}
          />
          <div>
            <h3 className="text-base font-semibold">
              {progress.blocking_incident ? "Sự cố đang chặn" : "Không có sự cố đang chặn"}
            </h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {progress.blocking_incident ?? progress.next_action}
            </p>
            {progress.blocking_incident ? (
              <p className="mt-2 text-sm font-medium">{progress.next_action}</p>
            ) : null}
          </div>
        </div>
      </Panel>

      <TechnicalAppendix>
        <SafeTechnicalJson
          value={{
            workflow_run_id: progress.workflow_run_id,
            project_id: progress.project_id,
            state: progress.state,
            active_stage: progress.active_stage,
            raw_stages: progress.stages,
            ...progress.technical_appendix
          }}
        />
      </TechnicalAppendix>
    </section>
  );
}

function ProgressMetric({
  icon,
  label,
  value,
  hint
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Panel className="min-h-36">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className="text-primary">{icon}</span>
      </div>
      <div className="mt-4">
        <FriendlyStatusBadge value={value} />
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">{hint}</p>
    </Panel>
  );
}

function friendlyStageLabel(stage: string) {
  const labels: Record<string, string> = {
    ADMISSION: "Tiếp nhận dự án",
    PACKAGE: "Chuẩn bị gói sản xuất",
    PACKAGE_READINESS: "Kiểm tra sẵn sàng",
    PROVIDER_EXECUTION: "Tạo nội dung và tài nguyên",
    MEDIA_PROVIDER: "Tạo tài nguyên media",
    RENDER: "Render video",
    TECHNICAL_QC: "Kiểm tra kỹ thuật",
    CREATIVE_QC: "Kiểm tra chất lượng nội dung",
    ARCHIVE: "Lưu và xác minh trên Drive",
    FINAL_MEDIA: "Chốt video cuối",
    FINAL_MEDIA_REF: "Chốt video cuối",
    FINAL_REVIEW: "Chuẩn bị xem video cuối",
    FINAL_REVIEW_CANDIDATE: "Chuẩn bị xem video cuối"
  };
  return labels[stage.toUpperCase()] ?? friendlyStatusLabel(stage);
}

function stageTimeSummary(startedAt?: string | null, finishedAt?: string | null) {
  if (!startedAt) return "Chưa bắt đầu";
  if (!finishedAt) return `Bắt đầu ${formatDateTime(startedAt)}`;
  return `${formatDateTime(startedAt)} → ${formatDateTime(finishedAt)}`;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(new Date(value));
}

function budgetHint(progress: ProductionProgress) {
  const formatter = (value?: number | null) =>
    value === null || value === undefined
      ? "chưa có"
      : new Intl.NumberFormat("vi-VN", {
          style: "currency",
          currency: progress.currency,
          maximumFractionDigits: 2
        }).format(value);
  return `Giữ ${formatter(progress.reserved_cost)} · đã đối soát ${formatter(
    progress.settled_cost
  )}`;
}
