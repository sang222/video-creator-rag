import { FriendlyStatusBadge } from "@/components/friendly-status-badge";

export function StatusBadge({ value }: { value: string | number | null | undefined }) {
  return <FriendlyStatusBadge value={value} />;
}
