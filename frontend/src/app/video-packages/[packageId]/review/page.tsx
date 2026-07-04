import { PackageReviewView } from "@/features/publishing/package-review-view";

export default function Page({ params }: { params: { packageId: string } }) {
  return <PackageReviewView packageId={params.packageId} />;
}
