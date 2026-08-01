import { PageHeader } from "@/components/cockpit";
import { LaunchCadenceView } from "@/features/launch/launch-cadence-dashboard";

export default async function Page({
  searchParams
}: {
  searchParams: Promise<{ channelId?: string | string[] }>;
}) {
  const params = await searchParams;
  const initialChannelId = Array.isArray(params.channelId)
    ? params.channelId[0]
    : params.channelId;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Launch và nhịp xuất bản long-form"
        subtitle="Theo dõi runway, buffer public-ready, publish slot và quyết định cadence deterministic của từng kênh."
        breadcrumbs={[
          { label: "Trung tâm", href: "/" },
          { label: "Launch cadence" }
        ]}
      />
      <LaunchCadenceView initialChannelId={initialChannelId} />
    </div>
  );
}
