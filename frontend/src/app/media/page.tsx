import { Database } from "lucide-react";

import { ActionHintCard, PageHeader } from "@/components/cockpit";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Tệp trên Google Drive"
        subtitle="File đã offload chỉ mở bằng nút Google Drive đã xác minh. VCOS không tạo link tải hoặc preview trung gian."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Tệp trên Google Drive" }]}
      />
      <ActionHintCard
        icon={Database}
        title="Mở trên Google Drive"
        body="Media card sẽ xuất hiện trong gói publish và chi tiết video sau khi file Drive được xác minh. Nếu chưa có file, hãy hoàn tất offload hoặc mở gói publish liên quan."
        href="/publishing"
        actionLabel="Đi tới gói publish"
      />
    </div>
  );
}
