import { PackageReviewView } from "@/features/publishing/package-review-view";

export default async function Page({ params }: { params: Promise<{ packageId: string }> }) {
  const { packageId } = await params;
  return <PackageReviewView packageId={packageId} />;
}
