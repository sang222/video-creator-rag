import { Panel } from "@/components/ui/panel";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Settings / Profiles / Policies</h1>
        <p className="mt-1 text-sm text-muted-foreground">Human edits create new profile versions and compiled snapshots.</p>
      </div>
      <Panel>
        <h2 className="text-base font-semibold">Policy Snapshot Rule</h2>
        <p className="mt-2 text-sm text-muted-foreground">Existing video projects keep old policy_snapshot_id. Future daily runs use the newly active snapshot only after human activation.</p>
      </Panel>
    </div>
  );
}
