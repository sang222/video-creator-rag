import { Database, ExternalLink, ShieldCheck } from "lucide-react";

import { ActionHintCard, EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";

export default function Page() {
  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Tệp Drive"
        subtitle="Tệp đã offload chỉ mở bằng nút Google Drive đã xác minh. VCOS không tạo link tải hoặc xem trước trung gian."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Tệp Drive" }]}
      />
      <div className="grid gap-4 md:grid-cols-3">
        <MetricSummaryCard icon={Database} label="Tệp Drive đang hiển thị" value="Chưa có dữ liệu" status="UNKNOWN" hint="Tệp thật xuất hiện trong gói publish hoặc chi tiết video sau khi xác minh." />
        <MetricSummaryCard icon={ShieldCheck} label="Cách truy cập" value="Google Drive" status="GOOGLE_DRIVE_READY" hint="Không hiển thị đường dẫn local, link tải trung gian hoặc xem trước trung gian." />
        <ActionHintCard
          icon={ExternalLink}
          title="Mở trên Google Drive"
          body="Media card sẽ xuất hiện trong gói publish và chi tiết video sau khi tệp Drive được xác minh."
          href="/publishing"
          actionLabel="Đi tới gói publish"
        />
      </div>
      <EmptyStateCard
        title="Chưa có tệp Drive để mở"
        description={'Khi file media đã được offload và xác minh, VCOS sẽ chỉ hiển thị CTA "Mở trên Google Drive". Trang này không tạo xem trước, link tải hoặc đường dẫn local.'}
        actions={[{ label: "Đi tới gói publish", href: "/publishing", variant: "primary" }]}
      />
    </div>
  );
}
