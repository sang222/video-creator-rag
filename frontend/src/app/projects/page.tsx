import { Panel } from "@/components/ui/panel";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Project Queue</h1>
        <p className="mt-1 text-sm text-muted-foreground">Projects keep explicit policy_snapshot_id lineage.</p>
      </div>
      <Panel>
        <h2 className="text-base font-semibold">Stage tracking</h2>
        <p className="mt-2 text-sm text-muted-foreground">Open a channel workspace to inspect projects, blockers, artifacts, publish state, diagnostics, and learning state.</p>
      </Panel>
    </div>
  );
}
