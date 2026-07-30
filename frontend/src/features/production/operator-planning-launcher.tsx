"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarCheck,
  Film,
  ListChecks,
  Play,
  ShieldCheck
} from "lucide-react";

import {
  EmptyStateCard,
  MetricSummaryCard,
  TechnicalAppendix
} from "@/components/cockpit";
import { LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { SafeTechnicalJson } from "@/features/production/production-cockpit-card";
import {
  getOperatorPlanningCatalog,
  prepareAndLaunchOperatorPlanningSource,
  queryKeys
} from "@/lib/api";
import type {
  OperatorPlanningLaunch,
  OperatorPlanningOption
} from "@/lib/types";

const controlClass =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20";

export function OperatorPlanningLauncher() {
  const queryClient = useQueryClient();
  const [selectedDailyId, setSelectedDailyId] = useState("");
  const [selectedLongId, setSelectedLongId] = useState("");
  const [maxBudgetInput, setMaxBudgetInput] = useState("0");
  const [result, setResult] = useState<OperatorPlanningLaunch | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const query = useQuery({
    queryKey: queryKeys.operatorPlanning,
    queryFn: getOperatorPlanningCatalog,
    retry: false
  });
  const dailyOptions = useMemo(
    () => query.data?.daily_short_options.filter((item) => item.launchable) ?? [],
    [query.data]
  );
  const longOptions = useMemo(
    () => query.data?.long_form_options.filter((item) => item.launchable) ?? [],
    [query.data]
  );
  const blockedOptions = useMemo(
    () =>
      [
        ...(query.data?.daily_short_options ?? []),
        ...(query.data?.long_form_options ?? [])
      ].filter((item) => !item.launchable),
    [query.data]
  );
  const selectedDaily = dailyOptions.find(
    (item) => item.source_id === selectedDailyId
  );
  const selectedLong = longOptions.find(
    (item) => item.source_id === selectedLongId
  );
  const maxBudgetUsd = Number(maxBudgetInput);
  const budgetValid =
    Number.isFinite(maxBudgetUsd) && maxBudgetUsd >= 0 && maxBudgetUsd <= 250;

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorPlanning }),
      queryClient.invalidateQueries({ queryKey: queryKeys.productionCockpit() }),
      queryClient.invalidateQueries({ queryKey: queryKeys.commandCenter })
    ]);
  };
  const dailyMutation = useMutation({
    mutationFn: () => {
      if (!selectedDaily) throw new Error("DAILY_SOURCE_REQUIRED");
      if (!budgetValid) throw new Error("SUPPORT_BUDGET_INVALID");
      return prepareAndLaunchOperatorPlanningSource({
        sourceType: selectedDaily.source_type,
        sourceId: selectedDaily.source_id,
        maxBudgetUsd
      });
    },
    onSuccess: async (launch) => {
      setResult(launch);
      setNotice(
        launch.reused_workflow
          ? "Đã tìm thấy workflow hiện có và giữ nguyên tiến độ."
          : "Đã tạo dự án typed v2 và xếp lịch workflow bền vững."
      );
      await refresh();
    },
    onError: () => {
      setNotice(
        "Chưa thể khởi động Short hằng ngày. Source vẫn được giữ nguyên; tải lại danh sách để xem điều kiện mới nhất."
      );
    }
  });
  const longMutation = useMutation({
    mutationFn: () => {
      if (!selectedLong) throw new Error("LONG_SOURCE_REQUIRED");
      if (!budgetValid) throw new Error("SUPPORT_BUDGET_INVALID");
      return prepareAndLaunchOperatorPlanningSource({
        sourceType: selectedLong.source_type,
        sourceId: selectedLong.source_id,
        maxBudgetUsd
      });
    },
    onSuccess: async (launch) => {
      setResult(launch);
      setNotice(
        launch.reused_workflow
          ? "Đã dùng lại workflow video dài hiện có."
          : "Đã khóa tiêu đề, tạo dự án typed v2 và xếp lịch workflow bền vững."
      );
      await refresh();
    },
    onError: () => {
      setNotice(
        "Chưa thể khởi động video dài. VCOS không thay đổi source hoặc tạo bằng chứng thay thế."
      );
    }
  });
  if (query.isLoading) {
    return <LoadingState label="Đang đọc nguồn kế hoạch đã đóng băng" />;
  }
  if (query.isError || !query.data) {
    return (
      <EmptyStateCard
        title="Chưa đọc được lịch sản xuất"
        description="Phiên hiện tại chưa thể đọc catalog typed v2. Hãy kiểm tra quyền production và thử lại; VCOS chưa tạo dự án nào."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        <MetricSummaryCard
          icon={Film}
          label="Short hằng ngày sẵn sàng"
          value={dailyOptions.length}
          hint="Gồm lịch Daily v2 hoặc proposal v2 có thể được backend hoàn tất từ persisted evidence; UI không gửi nội dung."
        />
        <MetricSummaryCard
          icon={CalendarCheck}
          label="Lịch video dài sẵn sàng"
          value={longOptions.length}
          hint="Preflight và support authority được backend tạo từ slot/evidence frozen; profile, policy, duration và destination luôn được resolve lại."
        />
        <MetricSummaryCard
          icon={ShieldCheck}
          label="Nguồn đang bị chặn"
          value={blockedOptions.length}
          status={blockedOptions.length ? "BLOCKED" : "READY"}
          hint="Nguồn bị chặn vẫn được hiển thị để người vận hành biết việc cần bổ sung."
        />
      </div>

      <Panel className="border-primary/30 bg-primary/[0.03]">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 text-primary" size={20} aria-hidden="true" />
          <div>
            <h2 className="font-semibold">Chuẩn bị và khởi động từ authority đã có</h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {query.data.safety_notice}
            </p>
            <label className="mt-4 block max-w-xs text-sm">
              <span className="mb-2 block font-medium">
                Trần chi phí chuẩn bị (USD)
              </span>
              <input
                aria-label="Trần chi phí chuẩn bị"
                className={controlClass}
                max="250"
                min="0"
                onChange={(event) => setMaxBudgetInput(event.target.value)}
                step="0.01"
                type="number"
                value={maxBudgetInput}
              />
              <span className="mt-1 block text-xs text-muted-foreground">
                Đây chỉ là trần chi phí. Route media trả phí vẫn bị cấm ở bước chuẩn bị.
              </span>
            </label>
          </div>
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <PlanningLanePanel
          title="Short hằng ngày"
          description="Chọn lịch hoặc proposal Daily Short v2. Backend tạo/reuse strict preflight từ evidence đã lưu rồi đóng băng successor support authority; proposal gốc không bị sửa."
          options={dailyOptions}
          selectedId={selectedDailyId}
          onSelect={setSelectedDailyId}
          emptyText="Chưa có Daily Short đủ điều kiện chuẩn bị. Kiểm tra proposal v2, PASS preflight, destination, category và hồ sơ kênh."
        >
          {selectedDaily ? <SourceSummary option={selectedDaily} /> : null}
          <div className="mt-4">
            <Button
              className="w-full"
              disabled={!selectedDaily || !budgetValid || dailyMutation.isPending}
              onClick={() => dailyMutation.mutate()}
              variant="primary"
            >
              <Play size={16} aria-hidden="true" />
              {dailyMutation.isPending
                ? "Đang chuẩn bị..."
                : selectedDaily?.workflow_run_id
                  ? "Dùng lại workflow Short"
                  : "Chuẩn bị và bắt đầu Short"}
            </Button>
          </div>
        </PlanningLanePanel>

        <PlanningLanePanel
          title="Video dài"
          description="Chọn lịch Long-form v2. Backend tạo/reuse strict preflight từ evidence đã lưu rồi đóng băng support authority; UI không gửi script hoặc bằng chứng."
          options={longOptions}
          selectedId={selectedLongId}
          onSelect={setSelectedLongId}
          emptyText="Chưa có lịch video dài đủ điều kiện chuẩn bị. Bổ sung PASS preflight, destination, category hoặc hồ sơ kênh trước."
        >
          {selectedLong ? <SourceSummary option={selectedLong} /> : null}
          <div className="mt-4">
            <Button
              className="w-full"
              disabled={!selectedLong || !budgetValid || longMutation.isPending}
              onClick={() => longMutation.mutate()}
              variant="primary"
            >
              <Play size={16} aria-hidden="true" />
              {longMutation.isPending
                ? "Đang chuẩn bị..."
                : selectedLong?.workflow_run_id
                  ? "Dùng lại workflow video dài"
                  : "Chuẩn bị và bắt đầu video dài"}
            </Button>
          </div>
        </PlanningLanePanel>
      </div>

      {notice ? (
        <Panel aria-live="polite" className="border-primary/30">
          <p className="text-sm">{notice}</p>
          {result ? (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button asChild variant="primary">
                <Link href={`/projects/${result.project_id}/production`}>
                  Mở tiến độ sản xuất
                </Link>
              </Button>
              <span className="text-xs text-muted-foreground">
                {result.next_action}
              </span>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {blockedOptions.length ? (
        <Panel>
          <div className="flex items-center gap-2">
            <ListChecks className="text-amber-400" size={18} aria-hidden="true" />
            <h2 className="font-semibold">Nguồn cần bổ sung trước khi chạy</h2>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {blockedOptions.slice(0, 8).map((option) => (
              <div
                className="rounded-md border border-border bg-background/35 p-3"
                key={`${option.lane}-${option.source_id}`}
              >
                <div className="text-sm font-medium">{option.title}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {option.company_label} · {option.channel_label} · {option.slot_label}
                </div>
                <p className="mt-2 text-sm leading-5 text-muted-foreground">
                  {option.guidance}
                </p>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <TechnicalAppendix>
        <SafeTechnicalJson
          value={{
            catalog: query.data.technical_appendix,
            selected_daily_source: selectedDaily?.technical_appendix ?? null,
            selected_long_source: selectedLong?.technical_appendix ?? null,
            last_launch: result?.technical_appendix ?? null
          }}
        />
      </TechnicalAppendix>
    </div>
  );
}

function PlanningLanePanel({
  title,
  description,
  options,
  selectedId,
  onSelect,
  emptyText,
  children
}: {
  title: string;
  description: string;
  options: OperatorPlanningOption[];
  selectedId: string;
  onSelect: (sourceId: string) => void;
  emptyText: string;
  children: React.ReactNode;
}) {
  return (
    <Panel>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
      {options.length ? (
        <>
          <label className="mt-4 block text-sm">
            <span className="mb-2 block font-medium">Nguồn kế hoạch đủ điều kiện</span>
            <select
              aria-label={`Chọn nguồn ${title}`}
              className={controlClass}
              onChange={(event) => onSelect(event.target.value)}
              value={selectedId}
            >
              <option value="">Chọn theo tên kênh và ngày lịch</option>
              {options.map((option) => (
                <option key={option.source_id} value={option.source_id}>
                  {option.title} — {option.company_label} / {option.channel_label} —{" "}
                  {option.slot_label}
                </option>
              ))}
            </select>
          </label>
          {children}
        </>
      ) : (
        <div className="mt-4 rounded-md border border-dashed border-border p-4 text-sm leading-6 text-muted-foreground">
          {emptyText}
        </div>
      )}
    </Panel>
  );
}

function SourceSummary({ option }: { option: OperatorPlanningOption }) {
  return (
    <div className="mt-4 grid gap-2 rounded-md border border-border bg-background/35 p-3 text-sm">
      <SummaryLine label="Tiêu đề đã khóa" value={option.title} />
      <SummaryLine label="Kênh" value={`${option.company_label} · ${option.channel_label}`} />
      <SummaryLine label="Lịch" value={option.slot_label} />
      <SummaryLine label="Phân công" value={option.assignment_label} />
      <SummaryLine label="Thời lượng" value={option.duration_label ?? "Chưa xác định"} />
      <SummaryLine label="Trạng thái" value={option.status_label} />
      <p className="pt-1 text-xs leading-5 text-muted-foreground">
        {option.guidance}
      </p>
    </div>
  );
}

function SummaryLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}
