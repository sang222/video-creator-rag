import { Database } from "lucide-react";

import { Panel } from "@/components/ui/panel";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Media Storage / Google Drive Offload</h1>
        <p className="mt-1 text-sm text-muted-foreground">File access stays on Google Drive web_view_link only.</p>
      </div>
      <Panel>
        <div className="flex items-start gap-3">
          <Database className="text-primary" size={22} />
          <div>
            <h2 className="text-base font-semibold">Open in Google Drive</h2>
            <p className="mt-2 text-sm text-muted-foreground">Dashboard media cards appear on publish and uploaded video screens after CloudMediaRef verification.</p>
          </div>
        </div>
      </Panel>
    </div>
  );
}
