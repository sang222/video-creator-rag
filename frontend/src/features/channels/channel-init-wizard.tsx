"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ErrorState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { initChannel, queryKeys } from "@/lib/api";

const schema = z.object({
  company_id: z.string().uuid(),
  key: z.string().min(1),
  name: z.string().min(1),
  template_key: z.string().min(1).default("saas_digital_leverage"),
  primary_language: z.string().min(2).default("en"),
  target_market: z.string().optional(),
  long_form_target_minutes: z.coerce.number().min(1).max(180),
  short_form_length_seconds: z.coerce.number().min(5).max(59),
  tts_character_budget: z.coerce.number().min(100).max(200000),
  ai_hero_budget_usd: z.coerce.number().min(0).max(175),
  derivative_shorts_per_long_form: z.coerce.number().min(0).max(10),
  drive_offload_enabled: z.boolean().default(true)
});

type FormValues = z.infer<typeof schema>;

export function ChannelInitWizard() {
  const queryClient = useQueryClient();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      template_key: "saas_digital_leverage",
      primary_language: "en",
      long_form_target_minutes: 12,
      short_form_length_seconds: 45,
      tts_character_budget: 18000,
      ai_hero_budget_usd: 12,
      derivative_shorts_per_long_form: 2,
      drive_offload_enabled: true
    }
  });
  const mutation = useMutation({
    mutationFn: initChannel,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Channel Init</h1>
        <p className="mt-1 text-sm text-muted-foreground">Human-entered config only. No AI upgrade suggestion.</p>
      </div>
      <Panel>
        <form className="grid gap-5 md:grid-cols-2" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
          <Field label="Company ID" error={form.formState.errors.company_id?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("company_id")} />
          </Field>
          <Field label="Template" error={form.formState.errors.template_key?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("template_key")} />
          </Field>
          <Field label="Channel Key" error={form.formState.errors.key?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("key")} />
          </Field>
          <Field label="Channel Name" error={form.formState.errors.name?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("name")} />
          </Field>
          <Field label="Language" error={form.formState.errors.primary_language?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("primary_language")} />
          </Field>
          <Field label="Region / Market" error={form.formState.errors.target_market?.message}>
            <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("target_market")} />
          </Field>
          <Field label="Long-form target minutes" error={form.formState.errors.long_form_target_minutes?.message}>
            <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("long_form_target_minutes")} />
          </Field>
          <Field label="Short length seconds" error={form.formState.errors.short_form_length_seconds?.message}>
            <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("short_form_length_seconds")} />
          </Field>
          <Field label="TTS character budget" error={form.formState.errors.tts_character_budget?.message}>
            <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("tts_character_budget")} />
          </Field>
          <Field label="AI hero budget USD" error={form.formState.errors.ai_hero_budget_usd?.message}>
            <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("ai_hero_budget_usd")} />
          </Field>
          <Field label="Derivative shorts per long-form" error={form.formState.errors.derivative_shorts_per_long_form?.message}>
            <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...form.register("derivative_shorts_per_long_form")} />
          </Field>
          <label className="flex min-h-10 items-center gap-3 rounded-md border border-border px-3 py-2 text-sm">
            <input type="checkbox" {...form.register("drive_offload_enabled")} />
            Drive offload enabled
          </label>
          <div className="md:col-span-2 flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" disabled={mutation.isPending}>
              <CheckCircle2 size={16} />
              Create, Compile, Activate
            </Button>
            <StatusBadge value="Human config only" />
          </div>
        </form>
      </Panel>
      {mutation.isError ? <ErrorState message={mutation.error.message} /> : null}
      {mutation.isSuccess ? (
        <Panel className="border-emerald-500/40">
          <h2 className="text-base font-semibold">Channel này đã sẵn sàng sản xuất video.</h2>
          <p className="mt-2 text-sm text-muted-foreground">Profile và policy snapshot đã được tạo từ thao tác của human operator.</p>
        </Panel>
      ) : null}
    </div>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">{label}</span>
      {children}
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}
