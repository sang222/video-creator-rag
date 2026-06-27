import { Badge } from "@/components/ui/badge";

const labels: Record<string, string> = {
  READY_FOR_HUMAN_REVIEW: "Chờ người duyệt",
  NEEDS_MORE_EVIDENCE: "Cần thêm bằng chứng",
  BLOCKED: "Đang bị chặn",
  APPROVED: "Đã duyệt",
  REJECTED: "Đã từ chối",
  STALE: "Dữ liệu cũ",
  UNKNOWN: "Chưa có dữ liệu",
  UNAVAILABLE: "Không khả dụng",
  NOT_AVAILABLE: "Không khả dụng",
  FRESH: "Dữ liệu mới",
  CURRENT: "Dữ liệu mới",
  NEEDS_AUTH: "Cần kết nối tài khoản",
  CONNECTED: "Đã kết nối",
  NOT_CONFIGURED: "Chưa cấu hình",
  FAILED: "Thất bại",
  VERIFIED: "Đã xác minh",
  CLEANED_LOCAL: "Đã dọn file local",
  CLEANED: "Đã dọn file local",
  ACTIVE: "Đang hoạt động",
  READY: "Sẵn sàng",
  DRAFT: "Bản nháp",
  PAUSED: "Đang tạm dừng",
  DEACTIVATED: "Đã ngừng",
  ARCHIVED: "Đã lưu trữ",
  HEALTHY: "Ổn định",
  WATCHLIST: "Cần theo dõi",
  LOW_VIEW: "Views thấp",
  NO_VIEW: "Chưa có views",
  NEEDS_HUMAN_REVIEW: "Cần người duyệt",
  ACTION_REQUIRED: "Cần xử lý",
  CHECK_FRESHNESS: "Kiểm tra độ mới",
  WATCH: "Theo dõi",
  HIGH: "Ưu tiên cao",
  NORMAL: "Bình thường",
  LOW: "Thấp",
  CRITICAL: "Khẩn cấp",
  HARD_RULE: "Quy tắc bắt buộc",
  HUMAN_REQUIRED: "Cần người vận hành",
  SEE_EVIDENCE: "Xem bằng chứng",
  SEE_DIAGNOSTIC: "Xem diagnostic",
  SYSTEM_RECORDED: "Hệ thống đã ghi",
  CONFIGURED: "Đã cấu hình",
  MISSING_REQUIRED_GAP: "Thiếu cấu hình bắt buộc",
  GOOGLE_DRIVE_READY: "Google Drive sẵn sàng",
  NO_CLOUD_MEDIA: "Chưa có file Drive",
  NO_PROJECTS: "Chưa có project",
  PROJECTS_ACTIVE: "Có project",
  NO_HANDOFFS: "Chưa có gói publish",
  HANDOFFS_AVAILABLE: "Có gói publish",
  NO_LEARNING_REVIEW: "Chưa có bài học",
  LEARNING_REVIEW_READY: "Có bài học chờ duyệt",
  INSIDE: "Trong khung giờ",
  OUTSIDE: "Ngoài khung giờ",
  OK: "Ổn",
  REVIEW: "Cần review",
  AVAILABLE: "Có sẵn",
  CHANGED: "Đã thay đổi",
  STRONG: "Mạnh",
  WEAK: "Yếu",
  OBSERVING: "Đang quan sát",
  DISABLED: "Đang tắt",
  CONFIGURED_BY_CATALOG: "Cấu hình bằng catalog",
  NO_AUTO_PUBLISH: "Không tự publish",
  DAILY_ALLOWED: "Được tạo daily",
  DAILY_BLOCKED: "Daily đang bị chặn",
  READ_ONLY: "Chỉ đọc",
  OWNER_ADMIN: "Chủ sở hữu/admin",
  CHANNEL_MANAGER: "Quản lý kênh",
  PRODUCER: "Producer",
  REVIEWER: "Reviewer",
  PUBLISHER: "Người xác nhận publish",
  ANALYST: "Analyst",
  PROCUREMENT_OPERATOR: "Ops mua sắm",
  COMPLIANCE_REVIEWER: "Duyệt compliance",
  LEARNING_REVIEWER: "Duyệt bài học"
};

function toneFor(value: string) {
  const normalized = value.toUpperCase();
  if (["ACTIVE", "READY", "HEALTHY", "CONNECTED", "VERIFIED", "CURRENT", "FRESH", "APPROVED", "OK", "PASS", "INSIDE"].includes(normalized)) return "success";
  if (["PAUSED", "WATCHLIST", "STALE", "UNKNOWN", "NEEDS_AUTH", "NEEDS_MORE_EVIDENCE", "REVIEW", "REVIEW_REQUIRED", "NEEDS_HUMAN_REVIEW"].includes(normalized)) return "warning";
  if (["BLOCKED", "BLOCK", "FAILED", "DEACTIVATED", "ARCHIVED", "MISSING_REQUIRED_GAP", "HIGH", "CRITICAL", "OUTSIDE"].includes(normalized)) return "danger";
  if (["WEAK", "STRONG", "OBSERVING", "CONFIGURED"].includes(normalized)) return "info";
  return "neutral";
}

export function friendlyStatusLabel(value: string | number | null | undefined) {
  const raw = value === null || value === undefined || value === "" ? "UNKNOWN" : String(value);
  return labels[raw.toUpperCase()] ?? raw.replaceAll("_", " ").toLowerCase();
}

export function FriendlyStatusBadge({ value }: { value: string | number | null | undefined }) {
  const raw = value === null || value === undefined || value === "" ? "UNKNOWN" : String(value);
  return <Badge tone={toneFor(raw)}>{friendlyStatusLabel(raw)}</Badge>;
}
