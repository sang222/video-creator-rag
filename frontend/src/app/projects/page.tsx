import { PageHeader } from "@/components/cockpit";
import { OperatorPlanningLauncher } from "@/features/production/operator-planning-launcher";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Dự án và lịch sản xuất"
        subtitle="Chọn nguồn kế hoạch typed v2 đã đủ điều kiện để tạo dự án và xếp lịch workflow trong một thao tác an toàn."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Dự án" }]}
      />
      <OperatorPlanningLauncher />
    </div>
  );
}
