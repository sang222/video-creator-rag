import Link from "next/link";
import { ArrowRight, CircleDollarSign, Film, Route, ShieldCheck } from "lucide-react";

import { EmptyStateCard, TechnicalAppendix } from "@/components/cockpit";
import {
  FriendlyStatusBadge,
  friendlyStatusLabel
} from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { NextVideo } from "@/lib/types";

export function ProductionCockpitCard({ nextVideo }: { nextVideo?: NextVideo | null }) {
  if (!nextVideo) {
    return (
      <EmptyStateCard
        title="Chưa có video tiếp theo"
        description="Khi lịch nội dung có dự án đủ điều kiện, VCOS sẽ chọn video tiếp theo theo quy tắc đã chốt. Không cần duyệt từng gate hoặc chạy lệnh thủ công."
        actions={[
          { label: "Xem kênh", href: "/channels" },
          { label: "Xem dự án", href: "/projects" }
        ]}
      />
    );
  }

  const assignmentContext = nextVideo.series_title
    ? [nextVideo.series_title, nextVideo.run_label, nextVideo.episode_label]
        .filter(Boolean)
        .join(" · ")
    : nextVideo.standalone_reason ?? "Dự án độc lập theo kế hoạch nội dung";

  return (
    <Panel className="border-primary/35 bg-gradient-to-br from-card to-primary/[0.04]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-primary">
            <Film size={16} aria-hidden="true" />
            Video tiếp theo
          </div>
          <h2 className="mt-2 text-xl font-semibold">{nextVideo.title}</h2>
          {nextVideo.topic ? (
            <p className="mt-1 text-sm text-muted-foreground">{nextVideo.topic}</p>
          ) : null}
        </div>
        <FriendlyStatusBadge value={nextVideo.production_state} />
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Summary label="Luồng sản xuất" value={friendlyStatusLabel(nextVideo.lane)} />
        <Summary label="Kiểu nội dung" value={friendlyStatusLabel(nextVideo.content_mode)} />
        <Summary
          label="Cách phân công"
          value={friendlyStatusLabel(nextVideo.assignment_mode)}
        />
        <Summary label="Đích đến" value={destinationText(nextVideo)} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <ContextBlock
          icon={<Route size={17} aria-hidden="true" />}
          label={nextVideo.series_title ? "Chuỗi / đợt / tập" : "Lý do làm video độc lập"}
          body={assignmentContext}
        />
        <ContextBlock
          icon={<ShieldCheck size={17} aria-hidden="true" />}
          label="Vì sao được chọn"
          body={nextVideo.why_selected}
        />
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatusSummary label="Bước hiện tại" value={nextVideo.current_stage} />
        <StatusSummary label="Nhà cung cấp" value={nextVideo.provider_status} />
        <StatusSummary label="Render" value={nextVideo.render_status} />
        <StatusSummary label="Lưu trữ" value={nextVideo.archive_status} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
        <div className="rounded-md border border-border bg-background/35 p-3">
          <div className="text-xs text-muted-foreground">Trở ngại chính</div>
          <div className="mt-1 text-sm font-medium">
            {nextVideo.blocker ?? "Không có trở ngại đang chặn"}
          </div>
          <div className="mt-2">
            <FriendlyStatusBadge value={nextVideo.incident_status} />
          </div>
        </div>
        <div className="rounded-md border border-border bg-background/35 p-3">
          <div className="text-xs text-muted-foreground">Việc tiếp theo</div>
          <div className="mt-1 text-sm font-medium">{nextVideo.next_action}</div>
          <div className="mt-2 text-xs text-muted-foreground">
            Thao tác người vận hành:{" "}
            <span className="font-medium text-foreground">
              {friendlyStatusLabel(nextVideo.operator_action)}
            </span>
          </div>
        </div>
        <div className="rounded-md border border-border bg-background/35 p-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <CircleDollarSign size={15} aria-hidden="true" />
            Chi phí
          </div>
          <div className="mt-1 text-sm font-medium">
            {formatMoney(nextVideo.actual_cost_so_far, nextVideo.currency)} đã dùng
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            Dự kiến {formatMoney(nextVideo.estimated_cost, nextVideo.currency)}
          </div>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs leading-5 text-muted-foreground">
          VCOS tự điều phối các bước kỹ thuật đã được phép. Người vận hành chỉ cần
          can thiệp khi có sự cố, khi video cuối sẵn sàng xem, hoặc khi xác nhận upload thủ
          công.
        </p>
        <Button asChild variant="primary">
          <Link href={`/projects/${nextVideo.project_id}/production`}>
            Xem tiến độ sản xuất
            <ArrowRight size={16} aria-hidden="true" />
          </Link>
        </Button>
      </div>

      <div className="mt-4">
        <TechnicalAppendix>
          <SafeTechnicalJson
            value={{
              project_id: nextVideo.project_id,
              workflow_run_id: nextVideo.workflow_run_id,
              lane: nextVideo.lane,
              content_mode: nextVideo.content_mode,
              assignment_mode: nextVideo.assignment_mode,
              operator_action: nextVideo.operator_action,
              ...nextVideo.technical_appendix
            }}
          />
        </TechnicalAppendix>
      </div>
    </Panel>
  );
}

export function SafeTechnicalJson({ value }: { value: Record<string, unknown> }) {
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-black/20 p-3 font-mono text-xs">
      {JSON.stringify(redactLocalPaths(value), null, 2)}
    </pre>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background/35 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}

function StatusSummary({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="rounded-md border border-border bg-background/35 p-3">
      <div className="mb-2 text-xs text-muted-foreground">{label}</div>
      <FriendlyStatusBadge value={value} />
    </div>
  );
}

function ContextBlock({
  icon,
  label,
  body
}: {
  icon: React.ReactNode;
  label: string;
  body: string;
}) {
  return (
    <div className="rounded-md border border-border bg-background/35 p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="text-primary">{icon}</span>
        {label}
      </div>
      <p className="mt-2 text-sm leading-6">{body}</p>
    </div>
  );
}

function destinationText(nextVideo: NextVideo) {
  return [nextVideo.destination_label, nextVideo.destination_handle]
    .filter(Boolean)
    .join(" · ");
}

function formatMoney(value: number | null | undefined, currency: string) {
  if (value === null || value === undefined) return "Chưa có dữ liệu";
  try {
    return new Intl.NumberFormat("vi-VN", {
      style: "currency",
      currency,
      maximumFractionDigits: 2
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

function redactLocalPaths(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactLocalPaths);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => {
        if (
          /(local_path|filesystem|working_dir|temp_path|source_path|output_path)/i.test(
            key
          )
        ) {
          return [key, "[đã ẩn đường dẫn local]"];
        }
        return [key, redactLocalPaths(item)];
      })
    );
  }
  if (
    typeof value === "string" &&
    (value.startsWith("/") || value.startsWith("file://"))
  ) {
    return "[đã ẩn đường dẫn local]";
  }
  return value;
}
