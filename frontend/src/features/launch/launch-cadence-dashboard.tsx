"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  FlaskConical,
  Gauge,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  Video
} from "lucide-react";

import {
  MetricSummaryCard,
  TechnicalAppendix
} from "@/components/cockpit";
import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  evaluateCadence,
  getChannels,
  getLaunchAnalyticsDashboard,
  getLaunchCadenceDashboard,
  pauseLaunchCadence,
  queryKeys,
  resumeLaunchCadence
} from "@/lib/api";
import type {
  CadenceDecision,
  LaunchCadenceDashboard
} from "@/lib/types";

const decisionLabels: Record<CadenceDecision, string> = {
  START_LONG_FORM_PRODUCTION: "Bắt đầu một video long-form",
  WAIT_BUFFER_FULL: "Chờ vì buffer public-ready đã đủ",
  WAIT_NO_ELIGIBLE_CANDIDATE: "Chờ ứng viên đủ điều kiện",
  WAIT_ACTIVE_PRODUCTION: "Chờ lượt sản xuất đang hoạt động",
  WAIT_OUTSIDE_PRODUCTION_HORIZON: "Chờ đến cửa sổ bắt đầu sản xuất",
  WAIT_BUDGET_BLOCKED: "Chờ xử lý blocker ngân sách",
  WAIT_POLICY_OR_RIGHTS_BLOCKED: "Chờ xử lý chính sách hoặc quyền",
  WAIT_QUALITY_BLOCKED: "Chờ xử lý blocker chất lượng",
  WAIT_LAUNCH_NOT_ACTIVE: "Chờ kích hoạt launch run"
};

export function LaunchCadenceView({
  initialChannelId
}: {
  initialChannelId?: string;
}) {
  const channelsQuery = useQuery({
    queryKey: queryKeys.channels,
    queryFn: getChannels
  });
  const selectedChannelId =
    initialChannelId ?? channelsQuery.data?.[0]?.id ?? "";

  if (channelsQuery.isLoading) {
    return <LoadingState label="Đang tải danh sách kênh" />;
  }
  if (channelsQuery.isError) {
    return <ErrorState message={channelsQuery.error.message} />;
  }
  if (!channelsQuery.data?.length) {
    return (
      <Panel>
        <h2 className="text-base font-semibold">Chưa có kênh để theo dõi</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Tạo và kích hoạt một kênh trước khi thiết lập launch run long-form.
        </p>
        <Button asChild className="mt-4" variant="primary">
          <Link href="/channels/new">Tạo kênh</Link>
        </Button>
      </Panel>
    );
  }

  return (
    <div className="space-y-5">
      <Panel>
        <label
          className="text-sm font-medium"
          htmlFor="launch-cadence-channel"
        >
          Kênh YouTube
        </label>
        <select
          id="launch-cadence-channel"
          className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm md:max-w-xl"
          value={selectedChannelId}
          onChange={(event) => {
            window.location.assign(
              `/projects?channelId=${encodeURIComponent(event.target.value)}`
            );
          }}
        >
          {channelsQuery.data.map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.name}
            </option>
          ))}
        </select>
        <p className="mt-2 text-xs text-muted-foreground">
          Chính sách cadence quyết định thời điểm sản xuất. Người vận hành chỉ
          tạm dừng, tiếp tục hoặc yêu cầu đánh giá lại.
        </p>
      </Panel>
      <LaunchCadenceDashboardView channelId={selectedChannelId} />
    </div>
  );
}

export function LaunchCadenceDashboardView({
  channelId
}: {
  channelId: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.launchCadence(channelId),
    queryFn: () => getLaunchCadenceDashboard(channelId),
    enabled: Boolean(channelId)
  });

  const refreshDashboard = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.launchCadence(channelId)
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.commandCenter }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.productionCockpit()
      })
    ]);
  };
  const pauseMutation = useMutation({
    mutationFn: () => pauseLaunchCadence(channelId),
    onSuccess: refreshDashboard
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeLaunchCadence(channelId),
    onSuccess: refreshDashboard
  });
  const evaluateMutation = useMutation({
    mutationFn: () => evaluateCadence(channelId),
    onSuccess: refreshDashboard
  });

  if (query.isLoading) {
    return <LoadingState label="Đang tải launch run và cadence" />;
  }
  if (query.isError) {
    return <ErrorState message={query.error.message} />;
  }
  if (!query.data) {
    return <LoadingState label="Đang tải launch run và cadence" />;
  }

  const dashboard = query.data;
  const mutationError =
    pauseMutation.error ?? resumeMutation.error ?? evaluateMutation.error;
  const mutationPending =
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    evaluateMutation.isPending;

  return (
    <div className="space-y-5">
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold">{dashboard.channel_name}</h2>
              <FriendlyStatusBadge value={dashboard.launch_state} />
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Chế độ launch có kiểm soát · ngày{" "}
              {dashboard.launch_day ?? "chưa bắt đầu"}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {dashboard.launch_state === "PAUSED" ? (
              <Button
                disabled={
                  mutationPending || !dashboard.permissions.can_resume
                }
                onClick={() => resumeMutation.mutate()}
                variant="primary"
              >
                <Play size={16} aria-hidden="true" />
                Tiếp tục cadence
              </Button>
            ) : (
              <Button
                disabled={
                  mutationPending || !dashboard.permissions.can_pause
                }
                onClick={() => pauseMutation.mutate()}
                variant="danger"
              >
                <Pause size={16} aria-hidden="true" />
                Tạm dừng cadence
              </Button>
            )}
            <Button
              disabled={
                mutationPending || !dashboard.permissions.can_evaluate
              }
              onClick={() => evaluateMutation.mutate()}
            >
              <RefreshCw
                className={evaluateMutation.isPending ? "animate-spin" : ""}
                size={16}
                aria-hidden="true"
              />
              Đánh giá cadence
            </Button>
          </div>
        </div>
        {mutationError ? (
          <p className="mt-4 text-sm text-rose-200">
            Không thể cập nhật cadence: {mutationError.message}
          </p>
        ) : null}
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          {dashboard.safety_notice}
        </p>
      </Panel>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard
          icon={Gauge}
          label="Runway ứng viên"
          value={`${dashboard.runway.idea_candidates}/${dashboard.runway.targets.idea_candidates}`}
          hint={`${dashboard.runway.preflight_passed}/${dashboard.runway.targets.preflight_passed} qua preflight · ${dashboard.runway.greenlit}/${dashboard.runway.targets.greenlit} greenlit`}
        />
        <MetricSummaryCard
          icon={ShieldCheck}
          label="Buffer public-ready"
          value={`${dashboard.public_ready_buffer.count}/${dashboard.public_ready_buffer.target}`}
          status={dashboard.public_ready_buffer.state}
          hint="Video đã đủ điều kiện public, chưa tự publish."
        />
        <MetricSummaryCard
          icon={Video}
          label="Video đã publish"
          value={dashboard.videos_published}
          hint={`${dashboard.runway.in_production} đang sản xuất · ${dashboard.runway.final_review_ready} chờ final review`}
        />
        <MetricSummaryCard
          icon={FlaskConical}
          label="Thí nghiệm hiện tại"
          value={experimentLabel(dashboard.current_experiment.phase)}
          hint={
            dashboard.current_experiment.primary_variable ??
            "Chưa có biến thử nghiệm"
          }
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Lịch cadence kế tiếp</h2>
          <div className="mt-4 space-y-4 text-sm">
            <InfoRow
              icon={CalendarClock}
              label="Publish slot"
              value={
                dashboard.next_publish_slot
                  ? `${formatDateTime(dashboard.next_publish_slot.publish_at)} · ${dashboard.next_publish_slot.timezone}`
                  : "Chưa có slot đủ điều kiện"
              }
            />
            <InfoRow
              icon={Play}
              label="Cửa sổ bắt đầu sản xuất"
              value={
                dashboard.next_production_start_window
                  ? `${formatDateTime(dashboard.next_production_start_window.opens_at)} — ${formatDateTime(dashboard.next_production_start_window.closes_at)}`
                  : "Chưa có cửa sổ được policy cho phép"
              }
            />
            <InfoRow
              icon={Video}
              label="Chuỗi đang hoạt động"
              value={
                dashboard.active_series.length
                  ? dashboard.active_series
                      .map((series) => series.display_name)
                      .join(", ")
                  : "Chưa có chuỗi hoạt động"
              }
            />
          </div>
        </Panel>

        <Panel>
          <h2 className="text-base font-semibold">Quyết định cadence gần nhất</h2>
          {dashboard.latest_evaluation ? (
            <div className="mt-4 space-y-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <FriendlyStatusBadge
                  value={dashboard.latest_evaluation.decision}
                />
                <span>
                  {decisionLabels[dashboard.latest_evaluation.decision]}
                </span>
              </div>
              <p className="text-muted-foreground">
                {formatDateTime(dashboard.latest_evaluation.evaluated_at)}
              </p>
              <ReasonCodes
                reasonCodes={dashboard.latest_evaluation.reason_codes}
              />
            </div>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              Chưa có evaluation. Yêu cầu đánh giá để ghi nhận một quyết định
              deterministic và reason codes.
            </p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Blocker và việc tiếp theo</h2>
          {dashboard.blockers.length ? (
            <ul className="mt-4 space-y-3">
              {dashboard.blockers.map((blocker) => (
                <li
                  key={`${blocker.code}-${blocker.message}`}
                  className="rounded-md border border-border bg-muted/30 p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <FriendlyStatusBadge value={blocker.severity} />
                    <span className="font-medium">{blocker.message}</span>
                  </div>
                  <code className="mt-2 block text-xs text-muted-foreground">
                    {blocker.code}
                  </code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              Không có blocker đang hoạt động.
            </p>
          )}
          <p className="mt-4 text-sm">
            <span className="font-medium">Việc tiếp theo:</span>{" "}
            {dashboard.next_action}
          </p>
        </Panel>

        <LongFormAnalyticsPanel channelId={channelId} />
      </div>

      <TechnicalAppendix>
        <dl className="grid gap-2 text-xs md:grid-cols-2">
          <TechnicalValue label="launch_run_id" value={dashboard.launch_run_id} />
          <TechnicalValue
            label="policy_version_id"
            value={dashboard.policy_version_id}
          />
          <TechnicalValue label="policy_hash" value={dashboard.policy_hash} />
          <TechnicalValue
            label="generated_at"
            value={dashboard.generated_at}
          />
        </dl>
        <pre className="overflow-x-auto rounded-md bg-muted/40 p-3 text-xs">
          {JSON.stringify(dashboard.technical_appendix, null, 2)}
        </pre>
      </TechnicalAppendix>
    </div>
  );
}

const metricLabels: Record<string, string> = {
  views: "Lượt xem",
  impressions: "Lượt hiển thị",
  click_through_rate: "Tỷ lệ nhấp",
  average_view_duration_seconds: "Thời lượng xem trung bình",
  average_view_percentage: "Tỷ lệ xem trung bình",
  watch_time_minutes: "Thời gian xem",
  likes: "Lượt thích",
  comments: "Bình luận",
  subscribers_gained: "Người đăng ký tăng",
  subscribers_lost: "Người đăng ký giảm"
};

function LongFormAnalyticsPanel({ channelId }: { channelId: string }) {
  const query = useQuery({
    queryKey: queryKeys.launchAnalytics(channelId),
    queryFn: () => getLaunchAnalyticsDashboard(channelId),
    enabled: Boolean(channelId),
    retry: false
  });

  if (query.isLoading) {
    return <Panel><p className="text-sm text-muted-foreground">Đang tải các mốc học sau upload.</p></Panel>;
  }
  if (query.isError || !query.data) {
    return (
      <Panel>
        <h2 className="text-base font-semibold">Học sau upload long-form</h2>
        <p className="mt-2 text-sm text-muted-foreground">Chưa có video long-form đã xác nhận upload để lập các mốc 24 giờ, 72 giờ, 7 ngày và 30 ngày.</p>
      </Panel>
    );
  }

  const dashboard = query.data;
  const metricEntries = Object.entries(dashboard.metrics).slice(0, 6);
  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Học sau upload long-form</h2>
          <p className="mt-2 text-sm text-muted-foreground">Chỉ dùng số liệu owner analytics đã gắn với từng mốc quan sát; dữ liệu thiếu được hiển thị là thiếu, không thay bằng ước lượng.</p>
        </div>
        <FriendlyStatusBadge value={dashboard.analytics_freshness} />
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {(["H24", "H72", "D7", "D30"] as const).map((windowType) => (
          <div key={windowType} className="rounded-md border border-border bg-muted/25 p-3">
            <p className="text-sm font-medium">{windowType === "H24" ? "24 giờ" : windowType === "H72" ? "72 giờ" : windowType === "D7" ? "7 ngày" : "30 ngày"}</p>
            <div className="mt-2"><FriendlyStatusBadge value={dashboard.windows_by_type[windowType] ?? "NOT_YET_SYNCED"} /></div>
          </div>
        ))}
      </div>
      {metricEntries.length ? (
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {metricEntries.map(([key, metric]) => (
            <div key={key} className="rounded-md border border-border p-3">
              <dt className="text-sm text-muted-foreground">{metricLabels[key] ?? key}</dt>
              <dd className="mt-1 text-xl font-semibold">{metric.value ?? "Chưa có"}</dd>
              <div className="mt-2"><FriendlyStatusBadge value={metric.availability.state ?? "UNKNOWN"} /></div>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">Chưa có metric owner analytics đủ điều kiện hiển thị. Hệ thống vẫn giữ các mốc theo lịch và báo rõ khi cần kết nối lại quyền truy cập.</p>
      )}
      {dashboard.incidents_or_exclusions ? <p className="mt-4 text-sm text-amber-700 dark:text-amber-300">Có {dashboard.incidents_or_exclusions} sự cố hoặc ngoại lệ liên quan; các dữ liệu đó không được dùng làm bài học tự động.</p> : null}
      <TechnicalAppendix>
        <div>Video đã publish: {dashboard.published_videos}</div>
        <div>Mốc bằng chứng tiếp theo: {dashboard.next_evidence_milestone ? new Date(dashboard.next_evidence_milestone).toLocaleString("vi-VN") : "Chưa có"}</div>
        <div>Metric không khả dụng: {dashboard.unavailable_metrics.length ? dashboard.unavailable_metrics.join(", ") : "Không có"}</div>
      </TechnicalAppendix>
    </Panel>
  );
}

export function LaunchCadenceSummaryCard({
  channelId
}: {
  channelId: string;
}) {
  const query = useQuery({
    queryKey: queryKeys.launchCadence(channelId),
    queryFn: () => getLaunchCadenceDashboard(channelId)
  });

  if (query.isLoading) {
    return <LoadingState label="Đang tải tóm tắt launch cadence" />;
  }
  if (query.isError || !query.data) {
    return (
      <Panel>
        <h2 className="text-base font-semibold">Launch cadence</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Chưa đọc được launch run của kênh. Mở dashboard cadence để xem chi
          tiết hoặc thiết lập run.
        </p>
        <Button asChild className="mt-4">
          <Link href={`/projects?channelId=${channelId}`}>
            Mở launch cadence
          </Link>
        </Button>
      </Panel>
    );
  }

  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">Launch cadence</h2>
            <FriendlyStatusBadge value={query.data.launch_state} />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Buffer {query.data.public_ready_buffer.count}/
            {query.data.public_ready_buffer.target} ·{" "}
            {query.data.next_publish_slot
              ? `slot ${formatDateTime(query.data.next_publish_slot.publish_at)}`
              : "chưa có publish slot"}
          </p>
        </div>
        <Button asChild>
          <Link href={`/projects?channelId=${channelId}`}>
            Mở dashboard cadence
          </Link>
        </Button>
      </div>
    </Panel>
  );
}

function ReasonCodes({ reasonCodes }: { reasonCodes: string[] }) {
  if (!reasonCodes.length) {
    return <p className="text-muted-foreground">Không có reason code bổ sung.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {reasonCodes.map((code) => (
        <code
          key={code}
          className="rounded-md border border-border bg-muted/40 px-2 py-1 text-xs"
        >
          {code}
        </code>
      ))}
    </div>
  );
}

function InfoRow({
  icon: Icon,
  label,
  value
}: {
  icon: typeof CalendarClock;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <Icon
        className="mt-0.5 shrink-0 text-primary"
        size={17}
        aria-hidden="true"
      />
      <div>
        <div className="text-muted-foreground">{label}</div>
        <div className="mt-1">{value}</div>
      </div>
    </div>
  );
}

function TechnicalValue({
  label,
  value
}: {
  label: string;
  value?: string | null;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="font-mono text-foreground">{value ?? "null"}</dd>
    </div>
  );
}

function experimentLabel(
  phase: LaunchCadenceDashboard["current_experiment"]["phase"]
) {
  const labels: Record<typeof phase, string> = {
    AUDIENCE_PROMISE_EVIDENCE: "Bằng chứng lời hứa khán giả",
    SERIES_PACKAGING_EXPERIMENT: "Thử nghiệm packaging chuỗi",
    ALLOCATION_PREPARATION: "Chuẩn bị phân bổ",
    COMPLETE: "Đã hoàn tất",
    NOT_STARTED: "Chưa bắt đầu"
  };
  return labels[phase];
}

function formatDateTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(parsed);
}
