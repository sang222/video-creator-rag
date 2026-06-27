import { QueuesView } from "@/features/queues/queues-view";

export default async function Page({ params }: { params: Promise<{ queueType: string }> }) {
  const { queueType } = await params;
  return <QueuesView queueType={queueType} />;
}
