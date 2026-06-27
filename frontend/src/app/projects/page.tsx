import { Boxes, GitBranch, ShieldCheck } from "lucide-react";

import { ActionHintCard, EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Dự án"
        subtitle="Mỗi dự án giữ snapshot chính sách rõ ràng. Dự án cũ không tự chuyển sang snapshot mới khi chỉnh hồ sơ kênh."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Dự án" }]}
      />
      <div className="grid gap-4 md:grid-cols-3">
        <MetricSummaryCard icon={GitBranch} label="Dự án đang hiển thị" value="Chưa có dữ liệu" status="UNKNOWN" hint="Trang này không tự gom dữ liệu khi chưa chọn kênh." />
        <MetricSummaryCard icon={ShieldCheck} label="Snapshot chính sách" value="Theo từng dự án" hint="Dự án cũ giữ snapshot đã gắn trước đó." />
        <ActionHintCard
          icon={Boxes}
          title="Xem dự án theo kênh"
          body="Mở kênh để xem dự án, điểm chặn, artifact, trạng thái publish, chẩn đoán và bài học liên quan."
          href="/channels"
          actionLabel="Mở kênh"
        />
      </div>
      <EmptyStateCard
        title="Chưa chọn kênh để xem dự án"
        description="Dự án được đặt trong từng kênh để giữ đúng snapshot chính sách và ngữ cảnh vận hành. Chọn một kênh để xem danh sách dự án thật."
        actions={[{ label: "Mở kênh", href: "/channels", variant: "primary" }]}
      />
    </div>
  );
}
