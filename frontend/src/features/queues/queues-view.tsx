"use client";

import Link from "next/link";
import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";

import { ApprovalCard } from "@/components/approval-card";
import { ActionHintCard, EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { getQueues, queryKeys } from "@/lib/api";

const filters = ["all", "learning", "publish", "recovery", "ops"];
const filterLabels: Record<string, string> = {
  all: "Tất cả",
  learning: "Bài học",
  publish: "Gói publish",
  recovery: "Phục hồi",
  ops: "Vận hành"
};

export function QueuesView({ queueType }: { queueType?: string }) {
  const active = queueType ?? "all";
  const copy = queueCopy(active);
  const query = useQuery({
    queryKey: queryKeys.queues(active),
    queryFn: () => getQueues(active === "all" ? undefined : active)
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải hàng chờ duyệt" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải hàng chờ duyệt" /></div>;
  const total = query.data.items.length;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title={copy.title}
        subtitle={copy.subtitle}
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: copy.title }]}
      />
      <Tabs.Root value={active}>
        <Tabs.List className="flex flex-wrap gap-2">
          {filters.map((filter) => (
            <Tabs.Trigger key={filter} value={filter} asChild className="rounded-md border border-border px-3 py-2 text-sm transition hover:border-primary/60 data-[state=active]:border-primary data-[state=active]:bg-muted data-[state=active]:text-primary">
              <Link href={filterHref(filter)}>{filterLabels[filter]}</Link>
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard label={copy.totalLabel} value={total} hint={copy.totalHint} />
        {query.data.summaries.length === 0 ? (
          <ActionHintCard title={copy.hintTitle} body={copy.hintBody} href={copy.hintHref} actionLabel={copy.hintAction} />
        ) : null}
        {query.data.summaries.map((summary) => (
          <MetricSummaryCard
            key={summary.queue_type}
            label={queueTypeLabel(summary.queue_type, summary.label)}
            value={summary.count}
            status={summary.priority}
            hint={summary.next_action}
          />
        ))}
      </div>
      {query.data.items.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {query.data.items.map((item) => (
            <ApprovalCard key={`${item.queue_type}-${item.queue_item_id ?? item.entity_id}`} item={item} />
          ))}
        </div>
      ) : (
        <EmptyStateCard
          title={copy.emptyTitle}
          description={copy.emptyDescription}
          actions={copy.emptyActions}
        />
      )}
    </div>
  );
}

function filterHref(filter: string) {
  if (filter === "all") return "/queues";
  if (filter === "publish") return "/publishing";
  if (filter === "learning") return "/learning";
  return `/queues/${filter}`;
}

function queueTypeLabel(queueType: string, fallback: string) {
  return {
    learning: "Bài học cần duyệt",
    publish: "Gói publish cần xác nhận",
    publish_confirmation: "Gói publish cần xác nhận",
    recovery: "Video cần recovery",
    ops: "Việc vận hành",
    ops_manual_action: "Việc vận hành"
  }[queueType] ?? fallback;
}

function queueCopy(active: string) {
  const copies: Record<string, {
    title: string;
    subtitle: string;
    totalLabel: string;
    totalHint: string;
    hintTitle: string;
    hintBody: string;
    hintHref: string;
    hintAction: string;
    emptyTitle: string;
    emptyDescription: string;
    emptyActions: Array<{ label: string; href: string; variant?: "primary" | "secondary" }>;
  }> = {
    publish: {
      title: "Gói publish",
      subtitle: "Mở gói publish, lấy CTA Google Drive và thông tin mô tả, upload thủ công lên YouTube, rồi quay lại nhập URL/video_id.",
      totalLabel: "Gói đang chờ",
      totalHint: "Chỉ tính gói publish do backend đưa vào hàng chờ.",
      hintTitle: "Chưa có gói publish mới",
      hintBody: "Khi gói render sẵn sàng và cần người upload thủ công, gói publish sẽ xuất hiện ở đây.",
      hintHref: "/uploaded-videos",
      hintAction: "Xem video đã upload",
      emptyTitle: "Chưa có gói publish cần xử lý",
      emptyDescription: "Hiện chưa có gói nào cần người vận hành upload thủ công. Khi có gói mới, hãy mở file bằng CTA Google Drive, publish trên YouTube, rồi nhập paste-back để VCOS theo dõi analytics và chẩn đoán.",
      emptyActions: [
        { label: "Xem video đã upload", href: "/uploaded-videos", variant: "primary" },
        { label: "Về Trung tâm", href: "/" }
      ]
    },
    learning: {
      title: "Bài học",
      subtitle: "Duyệt bài học dựa trên bằng chứng. VCOS không tự đổi hồ sơ, snapshot chính sách hoặc cấu hình kênh.",
      totalLabel: "Bài học đang chờ",
      totalHint: "Chỉ tính bài học đang nằm trong hàng chờ.",
      hintTitle: "Chưa có bài học mới",
      hintBody: "Khi M10 tạo learning candidate có bằng chứng, bài học sẽ xuất hiện để người vận hành duyệt.",
      hintHref: "/learning",
      hintAction: "Mở trang bài học",
      emptyTitle: "Chưa có bài học chờ duyệt",
      emptyDescription: "Bài học mới sẽ xuất hiện khi backend có candidate kèm gói bằng chứng. Hãy xem bằng chứng trước khi duyệt.",
      emptyActions: [{ label: "Về Trung tâm", href: "/" }]
    },
    recovery: {
      title: "Phục hồi",
      subtitle: "Xem đề xuất phục hồi và chỉ thực hiện thao tác an toàn được backend cho phép.",
      totalLabel: "Đề xuất đang chờ",
      totalHint: "Chỉ tính đề xuất recovery đang mở.",
      hintTitle: "Chưa có đề xuất recovery",
      hintBody: "Khi chẩn đoán tạo đề xuất phục hồi, VCOS sẽ đưa vào hàng chờ này với bằng chứng liên quan.",
      hintHref: "/uploaded-videos",
      hintAction: "Xem video đã upload",
      emptyTitle: "Chưa có video cần recovery",
      emptyDescription: "Hiện chưa có chẩn đoán hoặc đề xuất phục hồi cần người vận hành xử lý trong bộ lọc này.",
      emptyActions: [{ label: "Xem video đã upload", href: "/uploaded-videos", variant: "primary" }]
    },
    ops: {
      title: "Vận hành",
      subtitle: "Theo dõi thao tác thủ công và sự cố vận hành đã được backend ghi nhận.",
      totalLabel: "Việc vận hành đang chờ",
      totalHint: "Chỉ tính manual action hoặc incident đang mở.",
      hintTitle: "Chưa có việc vận hành",
      hintBody: "Khi có incident hoặc thao tác thủ công cần xử lý, VCOS sẽ đưa lên đây.",
      hintHref: "/ops",
      hintAction: "Mở vận hành",
      emptyTitle: "Không có việc ops đang mở",
      emptyDescription: "Hiện chưa có incident hoặc manual action cần người vận hành xử lý trong bộ lọc này.",
      emptyActions: [{ label: "Mở vận hành", href: "/ops", variant: "primary" }]
    }
  };
  return copies[active] ?? {
    title: "Hàng chờ duyệt",
    subtitle: "Các việc cần người vận hành xem xét. Chỉ hiển thị action được backend cho phép.",
    totalLabel: "Việc đang chờ",
    totalHint: "Không có item nghĩa là chưa có việc cần người duyệt trong bộ lọc này.",
    hintTitle: "Chưa có việc mới",
    hintBody: "Khi backend tạo approval, gói publish, đề xuất phục hồi hoặc thao tác ops, việc đó sẽ xuất hiện tại đây.",
    hintHref: "/",
    hintAction: "Về Trung tâm",
    emptyTitle: "Không có việc trong hàng chờ",
    emptyDescription: "Hiện chưa có approval, gói publish, đề xuất phục hồi hoặc thao tác vận hành cần xử lý trong bộ lọc này. Khi backend tạo item mới, nó sẽ xuất hiện tại đây với việc tiếp theo rõ ràng.",
    emptyActions: [{ label: "Về Trung tâm", href: "/" }]
  };
}
