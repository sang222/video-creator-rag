"use client";

import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ActionCard } from "@/components/action-card";
import { PageHeader } from "@/components/cockpit";
import { MetricCard } from "@/components/metric-card";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { getCommandCenter, queryKeys } from "@/lib/api";

export function CommandCenterView() {
  const query = useQuery({ queryKey: queryKeys.commandCenter, queryFn: getCommandCenter });

  if (query.isLoading) return <LoadingState label="Đang tải Trung tâm điều hành" />;
  if (query.isError) return <ErrorState message={query.error.message} />;
  if (!query.data) return <LoadingState label="Đang tải Trung tâm điều hành" />;

  const data = query.data;
  const chartData = data.cards.map((card) => ({ name: card.title.split(" ")[0], count: card.count }));

  return (
    <div className="space-y-6 p-4 md:p-8">
      <section>
        <PageHeader
          title="Trung tâm điều hành"
          subtitle="Xem việc cần xử lý trước, rồi hành động dựa trên bằng chứng. Bảng điều hành không tự publish/upload/reupload."
          meta={<span className="text-xs text-muted-foreground">Cập nhật lúc {new Date(data.generated_at).toLocaleTimeString("vi-VN")}</span>}
        />
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.cards.map((card) => (
            <ActionCard key={card.key} card={card} />
          ))}
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
        <Panel>
          <PanelHeader>
            <PanelTitle>Tải việc vận hành</PanelTitle>
            <StatusBadge value="Không hiện raw log" />
          </PanelHeader>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <XAxis dataKey="name" stroke="#9aa6b2" />
                <YAxis allowDecimals={false} stroke="#9aa6b2" />
                <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "#151b20", border: "1px solid #33414b" }} />
                <Bar dataKey="count" fill="#2fc7a3" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <div className="grid gap-4">
          {data.metrics.map((metric) => (
            <MetricCard key={metric.key} label={metric.label} value={metric.value} state={metric.state} nextAction={metric.next_action} />
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {data.safety_warnings.map((warning) => (
          <Panel key={warning.key} className="border-amber-500/30">
            <div className="flex items-start justify-between gap-3">
              <h2 className="text-sm font-semibold">{warning.label}</h2>
              <StatusBadge value={warning.severity} />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{warning.text}</p>
          </Panel>
        ))}
      </section>
    </div>
  );
}
