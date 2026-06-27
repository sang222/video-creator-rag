import { ChannelWorkspaceView } from "@/features/channels/channel-workspace-view";

export default async function Page({ params }: { params: Promise<{ channelId: string }> }) {
  const { channelId } = await params;
  return <ChannelWorkspaceView channelId={channelId} />;
}
