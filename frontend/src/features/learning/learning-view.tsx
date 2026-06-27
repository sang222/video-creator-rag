"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
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

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading learning review" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading learning review" /></div>;

  const learningItems = query.data.items.filter((item) => item.entity_type === "learning_candidate");

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Learning Review / Playbook Promotion</h1>
        <p className="mt-1 text-sm text-muted-foreground">Approval creates audited playbook guidance only. It does not mutate channel profile config.</p>
      </div>
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
            <EmptyState title="No learning candidates" body="Bài học mới sẽ xuất hiện khi M10 creates evidence-backed queue items." />
          )}
        </div>
        <Panel>
          <h2 className="text-base font-semibold">Decision</h2>
          <form className="mt-4 space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Candidate ID</span>
              <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("candidate_id")} />
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Action</span>
              <select className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("action")}>
                {["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"].map((action) => (
                  <option key={action} value={action}>{action.replaceAll("_", " ")}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Role</span>
              <select className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("actor_role")}>
                <option value="LEARNING_REVIEWER">Learning Reviewer</option>
                <option value="OWNER_ADMIN">Owner/Admin</option>
                <option value="READ_ONLY_OBSERVER">Read-only Observer</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-2 block text-muted-foreground">Rationale</span>
              <textarea className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("rationale")} />
            </label>
            <Button type="submit" variant="primary" disabled={mutation.isPending}>Record decision</Button>
          </form>
          {mutation.isError ? <p className="mt-3 text-sm text-rose-100">{mutation.error.message}</p> : null}
          {mutation.isSuccess ? <p className="mt-3 text-sm text-emerald-100">Decision recorded with audit refs.</p> : null}
        </Panel>
      </section>
    </div>
  );
}
