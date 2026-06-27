import { Badge } from "@/components/ui/badge";

function toneFor(value: string) {
  const normalized = value.toUpperCase();
  if (["ACTIVE", "READY", "HEALTHY", "CONNECTED", "VERIFIED", "CURRENT", "APPROVED", "OK"].includes(normalized)) return "success";
  if (["PAUSED", "WATCHLIST", "STALE", "UNKNOWN", "NEEDS_AUTH", "NEEDS_MORE_EVIDENCE"].includes(normalized)) return "warning";
  if (["BLOCKED", "FAILED", "DEACTIVATED", "ARCHIVED", "MISSING_REQUIRED_GAP", "HIGH", "CRITICAL"].includes(normalized)) return "danger";
  if (["WEAK", "STRONG", "FRESH", "OBSERVING"].includes(normalized)) return "info";
  return "neutral";
}

export function StatusBadge({ value }: { value: string | number | null | undefined }) {
  const label = value === null || value === undefined || value === "" ? "UNKNOWN" : String(value);
  return <Badge tone={toneFor(label)}>{label.replaceAll("_", " ")}</Badge>;
}
