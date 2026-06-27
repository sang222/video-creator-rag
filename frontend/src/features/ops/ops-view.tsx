"use client";

import { useQuery } from "@tanstack/react-query";

import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getProviderOps, queryKeys } from "@/lib/api";

export function OpsView() {
  const query = useQuery({ queryKey: queryKeys.providerOps, queryFn: getProviderOps });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading provider health" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading provider health" /></div>;

  const data = query.data;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Provider / Cost / Ops Health</h1>
        <p className="mt-1 text-sm text-muted-foreground">Dashboard load is read-only and does not call real providers.</p>
      </div>
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {Object.entries(data.integrations).map(([key, value]) => (
          <Panel key={key}>
            <div className="text-sm text-muted-foreground">{key.replaceAll("_", " ")}</div>
            <div className="mt-3"><StatusBadge value={String(value.state ?? "UNKNOWN")} /></div>
          </Panel>
        ))}
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Providers</h2>
          <div className="mt-4 space-y-3">
            {data.providers.length ? data.providers.map((provider, index) => (
              <div key={index} className="rounded-md border border-border p-3 text-sm">
                <div className="font-medium">{String(provider.provider_key)}</div>
                <div className="mt-2"><StatusBadge value={String(provider.status ?? "UNKNOWN")} /></div>
              </div>
            )) : <p className="text-sm text-muted-foreground">Seed provider catalogs to populate this panel.</p>}
          </div>
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Manual Actions / Incidents</h2>
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            {[...data.incidents, ...data.manual_actions].length ? [...data.incidents, ...data.manual_actions].map((item, index) => (
              <div key={index} className="rounded-md border border-border p-3">{String(item.next_action ?? item.incident_type ?? item.action_type)}</div>
            )) : "No open ops items."}
          </div>
        </Panel>
      </section>
    </div>
  );
}
