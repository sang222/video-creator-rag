import { ProjectProductionView } from "@/features/production/project-production-view";

export default async function Page({
  params
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <ProjectProductionView projectId={projectId} />;
}
