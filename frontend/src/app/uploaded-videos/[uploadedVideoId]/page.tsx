import { UploadedVideoDetail } from "@/features/uploaded-videos/uploaded-video-detail";

export default async function Page({ params }: { params: Promise<{ uploadedVideoId: string }> }) {
  const { uploadedVideoId } = await params;
  return <UploadedVideoDetail uploadedVideoId={uploadedVideoId} />;
}
