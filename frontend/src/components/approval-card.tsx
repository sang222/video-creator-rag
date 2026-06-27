import { Clock, FileText } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { ApprovalQueueItem } from "@/lib/types";

const queueLabels: Record<string, string> = {
  learning: "Bài học chờ duyệt",
  publish_confirmation: "Gói publish",
  recovery: "Đề xuất phục hồi",
  ops_manual_action: "Thao tác ops"
};

const actionLabels: Record<string, string> = {
  APPROVE: "Duyệt",
  REJECT: "Từ chối",
  REQUEST_MORE_EVIDENCE: "Cần thêm bằng chứng",
  SUPPRESS: "Ẩn khỏi hàng chờ",
  EXPIRE: "Đánh dấu hết hạn",
  OPEN_DRIVE: "Mở Google Drive",
  CONFIRM_MANUAL_PUBLISH: "Xác nhận publish thủ công",
  ACCEPT: "Chấp nhận",
  WAIT: "Chờ thêm",
  REQUEST_REVIEW: "Yêu cầu review",
  MARK_UNSAFE: "Đánh dấu không an toàn",
  COMPLETE: "Hoàn tất",
  ADD_NOTE: "Thêm ghi chú"
};

export function ApprovalCard({ item, onAction }: { item: ApprovalQueueItem; onAction?: (action: string) => void }) {
  return (
    <Panel className="min-h-48">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
            <FileText size={14} />
            {queueLabels[item.queue_type] ?? "Hàng chờ"}
          </div>
          <h3 className="mt-2 text-base font-semibold">{item.operator_summary}</h3>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge value={item.priority} />
          <StatusBadge value={item.risk_level} />
          <StatusBadge value={item.confidence_label} />
        </div>
      </div>
      <p className="mt-3 text-sm text-muted-foreground">{item.friendly_status}</p>
      <div className="mt-4 rounded-md border border-border bg-background p-3 text-sm">
        <div className="font-medium">Bằng chứng</div>
        <div className="mt-1 text-muted-foreground">{item.evidence_summary}</div>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Clock size={14} />
          {item.due_at ? new Date(item.due_at).toLocaleString("vi-VN") : "Chưa có hạn xử lý"}
        </div>
        <div className="flex flex-wrap gap-2">
          {item.allowed_actions.slice(0, 4).map((action) => (
            <Button key={action} type="button" variant="secondary" onClick={() => onAction?.(action)}>
              {actionLabels[action] ?? action.toLowerCase().replaceAll("_", " ")}
            </Button>
          ))}
        </div>
      </div>
      <p className="mt-3 text-sm text-primary">{item.next_action}</p>
    </Panel>
  );
}
