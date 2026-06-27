import { ActionHintCard, PageHeader } from "@/components/cockpit";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Dự án video"
        subtitle="Mỗi dự án giữ snapshot chính sách rõ ràng. Dự án cũ không tự chuyển sang snapshot mới khi chỉnh hồ sơ kênh."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Dự án video" }]}
      />
      <ActionHintCard
        title="Xem dự án theo kênh"
        body="Mở không gian kênh để xem dự án, blocker, artifact, trạng thái publish, diagnostic và bài học liên quan."
        href="/channels"
        actionLabel="Mở không gian kênh"
      />
    </div>
  );
}
