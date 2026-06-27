import { ActionHintCard, PageHeader } from "@/components/cockpit";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Hồ sơ & chính sách"
        subtitle="Chỉnh sửa do người vận hành thực hiện sẽ tạo profile version và compiled snapshot mới. Không có config upgrade suggestion tự động."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Hồ sơ & chính sách" }]}
      />
      <ActionHintCard
        title="Quy tắc policy snapshot"
        body="Dự án video hiện có vẫn giữ snapshot chính sách cũ. Daily run tương lai chỉ dùng snapshot mới sau khi người vận hành kích hoạt."
        href="/channels"
        actionLabel="Mở không gian kênh"
      />
    </div>
  );
}
