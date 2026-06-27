"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyStateCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { decideLearningCandidate, getQueues, queryKeys } from "@/lib/api";

const decisionSchema = z.object({
  candidate_id: z.string().uuid(),
  action: z.enum(["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"]),
  actor_role: z.string().default("LEARNING_REVIEWER"),
  rationale: z.string().optional()
});

type DecisionValues = z.infer<typeof decisionSchema>;
const actionLabels: Record<DecisionValues["action"], string> = {
  APPROVE: "Duyệt",
  REJECT: "Từ chối",
  REQUEST_MORE_EVIDENCE: "Cần thêm bằng chứng",
  SUPPRESS: "Ẩn khỏi hàng chờ",
  EXPIRE: "Đánh dấu hết hạn"
};

export function LearningView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.queues("learning"), queryFn: () => getQueues("learning") });
  const form = useForm<DecisionValues>({
    resolver: zodResolver(decisionSchema),
    defaultValues: { action: "REQUEST_MORE_EVIDENCE", actor_role: "LEARNING_REVIEWER" }
  });
  const mutation = useMutation({
    mutationFn: (values: DecisionValues) => decideLearningCandidate(values.candidate_id, values),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.queues("learning") });
    }
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải bài học chờ duyệt" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải bài học chờ duyệt" /></div>;

  const learningItems = query.data.items.filter((item) => item.entity_type === "learning_candidate");

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Bài học chờ duyệt"
        subtitle="Duyệt bài học chỉ tạo playbook entry có audit. VCOS không tự đổi hồ sơ, policy snapshot hoặc cấu hình kênh."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Bài học chờ duyệt" }]}
      />
      <section className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          {learningItems.length ? (
            learningItems.map((item) => (
              <ApprovalCard
                key={item.queue_item_id ?? item.entity_id}
                item={item}
                onAction={(action) => {
                  if (item.entity_id) {
                    form.setValue("candidate_id", item.entity_id);
                    form.setValue("action", action as DecisionValues["action"]);
                  }
                }}
              />
            ))
          ) : (
            <EmptyStateCard title="Chưa có bài học chờ duyệt" description="Bài học mới sẽ xuất hiện khi M10 tạo queue item có bằng chứng. Khi có dữ liệu, hãy xem evidence bundle trước khi duyệt." />
          )}
        </div>
        <Panel>
          <h2 className="text-base font-semibold">Quyết định</h2>
          <form className="mt-4 space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">ID bài học</span>
              <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("candidate_id")} />
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Hành động</span>
              <select className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("action")}>
                {(Object.keys(actionLabels) as DecisionValues["action"][]).map((action) => (
                  <option key={action} value={action}>{actionLabels[action]}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Vai trò hiện tại</span>
              <select className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("actor_role")}>
                <option value="LEARNING_REVIEWER">Duyệt bài học</option>
                <option value="OWNER_ADMIN">Chủ sở hữu/admin</option>
                <option value="READ_ONLY_OBSERVER">Chỉ đọc</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Lý do</span>
              <textarea className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("rationale")} />
            </label>
            <Button type="submit" variant="primary" disabled={mutation.isPending}>Ghi quyết định</Button>
          </form>
          {mutation.isError ? <p className="mt-3 text-sm text-rose-100">{mutation.error.message}</p> : null}
          {mutation.isSuccess ? <p className="mt-3 text-sm text-emerald-100">Quyết định đã được ghi cùng audit refs.</p> : null}
        </Panel>
      </section>
    </div>
  );
}
