"use client";

import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ActionCard } from "@/components/action-card";
import { MetricCard } from "@/components/metric-card";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel, PanelHeader, PanelTitle } from "@/components/ui/panel";
import { getCommandCenter, queryKeys } from "@/lib/api";

export function CommandCenterView() {
  const query = useQuery({ queryKey: queryKeys.commandCenter, queryFn: getCommandCenter });

  if (query.isLoading) return <LoadingState label="Loading Command Center" />;
  if (query.isError) return <ErrorState message={query.error.message} />;
  if (!query.data) return <LoadingState label="Loading Command Center" />;

  const data = query.data;
  const chartData = data.cards.map((card) => ({ name: card.title.split(" ")[0], count: card.count }));

  return (
    <div className="space-y-6 p-4 md:p-8">
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">Portfolio Command Center</h1>
            <p className="mt-1 text-sm text-muted-foreground">Know what needs attention, then act with evidence.</p>
          </div>
          <StatusBadge value={`Generated ${new Date(data.generated_at).toLocaleTimeString()}`} />
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.cards.map((card) => (
            <ActionCard key={card.key} card={card} />
          ))}
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
        <Panel>
          <PanelHeader>
            <PanelTitle>Action Load</PanelTitle>
            <StatusBadge value="No raw logs" />
          </PanelHeader>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <XAxis dataKey="name" stroke="#9a907f" />
                <YAxis allowDecimals={false} stroke="#9a907f" />
                <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "#241f18", border: "1px solid #4a4034" }} />
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
