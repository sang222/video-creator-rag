"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Search, Settings2, ShieldCheck } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useForm, type UseFormRegisterReturn } from "react-hook-form";
import { z } from "zod";

import { PageHeader } from "@/components/cockpit";
import { ErrorState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  activateChannel,
  compileChannelInitDraft,
  createChannelInitDraft,
  createCompany,
  getCompanies,
  initChannel,
  queryKeys,
  researchChannelInitDraft,
  reviewChannelInitDraft
} from "@/lib/api";
import type { ChannelContractDraft, ChannelInitCompileResult, ChannelInitDraft, Company, FieldMeta } from "@/lib/types";

const requiredText = z.string().trim().min(1, "Bắt buộc nhập.");

const minimalSchema = z.object({
  company_id: z.string().uuid("Chọn công ty."),
  channel_name: requiredText,
  public_presence_mode: z.enum(["EXISTING_PUBLIC_CHANNEL", "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT"]),
  youtube_url_or_handle: z.string().default(""),
  website_url: z.string().default(""),
  social_profile_links: z.string().default(""),
  operator_note_purpose: requiredText,
  intended_content_language: z.string().default(""),
  intended_primary_market: z.string().default(""),
  owner_operator_language: z.string().default("vi-VN"),
  initial_topic_pillar_hints: z.string().default(""),
  source_usage_attestation: z.boolean().default(false)
}).refine((value) => value.source_usage_attestation, {
  message: "Cần xác nhận quyền dùng nguồn trước khi research.",
  path: ["source_usage_attestation"]
}).refine((value) => {
  if (value.public_presence_mode === "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT") return true;
  return Boolean(value.youtube_url_or_handle.trim() || value.website_url.trim() || splitLines(value.social_profile_links).length);
}, {
  message: "Kênh đã có footprint công khai cần ít nhất một nguồn công khai.",
  path: ["youtube_url_or_handle"]
});

type MinimalFormValues = z.infer<typeof minimalSchema>;

const advancedSchema = z.object({
  company_id: z.string().uuid("Chọn công ty."),
  key: requiredText,
  name: requiredText,
  template_key: requiredText.default("saas_digital_leverage"),
  channel_type: requiredText.default("YOUTUBE_CHANNEL"),
  niche: requiredText,
  positioning: requiredText,
  brand_promise: requiredText,
  secondary_platforms: z.string().default("Shorts"),
  primary_persona: requiredText,
  audience_level: z.enum(["non_technical", "semi_technical", "technical", "expert"]),
  pain_points: requiredText,
  desired_outcome: requiredText,
  audience_notes: z.string().default(""),
  primary_market: z.enum(["US", "UK", "EU", "JP", "KR", "VN", "AU", "CA", "OTHER"], { required_error: "Chọn thị trường chính." }),
  secondary_markets: z.string().default(""),
  audience_locale: z.enum(["en-US", "en-GB", "ja-JP", "ko-KR", "vi-VN", "other"], { required_error: "Chọn locale người xem." }),
  content_language: requiredText,
  operator_language: requiredText.default("vi"),
  timezone: requiredText,
  currency: requiredText,
  measurement_units: z.enum(["metric", "imperial", "imperial_or_mixed"]),
  date_format: z.enum(["MM/DD/YYYY", "DD/MM/YYYY", "YYYY/MM/DD", "YYYY.MM.DD"]),
  cultural_tone: requiredText,
  cultural_formality: requiredText,
  cultural_humor: requiredText,
  cta_style: requiredText,
  market_examples_preference: z.enum(["prefer", "avoid"]),
  finance_claim_sensitivity: requiredText,
  health_claim_sensitivity: requiredText,
  disclosure_standard: requiredText,
  content_pillars: requiredText,
  allowed_angles: requiredText,
  forbidden_angles: requiredText,
  allowed_topics: requiredText,
  forbidden_topics: requiredText,
  long_form_enabled: z.boolean().default(true),
  long_form_min_minutes: z.coerce.number().min(1).max(180),
  long_form_max_minutes: z.coerce.number().min(1).max(180),
  shorts_enabled: z.boolean().default(true),
  shorts_min_seconds: z.coerce.number().min(5).max(59),
  shorts_max_seconds: z.coerce.number().min(5).max(59),
  shorts_hard_max_seconds: z.coerce.number().min(5).max(59).default(59),
  captions_required: z.boolean().default(true),
  chapters_required_for_long_form: z.boolean().default(true),
  derivative_shorts_per_long_form: z.coerce.number().min(0).max(10),
  narration_tone: z.enum(["documentary_explainer", "practical_explainer", "calm_professional", "investigative"]),
  pacing: z.enum(["clear_short_sentences", "moderate", "fast"]),
  allowed_style: requiredText,
  forbidden_style: requiredText,
  cost_sensitivity: z.enum(["low", "medium", "high"]),
  avoid_unnecessary_ai_hero: z.boolean().default(true),
  prefer_reuse_safe_assets: z.boolean().default(true),
  exact_cost_claim_requires_provider_snapshot: z.boolean().default(true),
  min_evidence_required: requiredText,
  reused_content_sensitivity: z.enum(["low", "medium", "high"]),
  drive_offload_enabled: z.boolean().default(true)
}).refine((value) => value.long_form_enabled || value.shorts_enabled, {
  message: "Bật ít nhất một format.",
  path: ["long_form_enabled"]
}).refine((value) => value.long_form_max_minutes >= value.long_form_min_minutes, {
  message: "Max phải lớn hơn hoặc bằng min.",
  path: ["long_form_max_minutes"]
}).refine((value) => value.shorts_max_seconds >= value.shorts_min_seconds, {
  message: "Max phải lớn hơn hoặc bằng min.",
  path: ["shorts_max_seconds"]
});

type FormValues = z.infer<typeof advancedSchema>;

export function ChannelInitWizard() {
  const [mode, setMode] = useState<"minimal" | "advanced">("minimal");

  if (mode === "advanced") {
    return (
      <div className="space-y-4">
        <div className="px-4 pt-4 md:px-8 md:pt-8">
          <Button type="button" onClick={() => setMode("minimal")}>
            <Search size={16} />
            Quay lại flow research tối thiểu
          </Button>
        </div>
        <AdvancedManualChannelInitForm />
      </div>
    );
  }

  return <MinimalResearchChannelInitWizard onAdvanced={() => setMode("advanced")} />;
}

function MinimalResearchChannelInitWizard({ onAdvanced }: { onAdvanced: () => void }) {
  const queryClient = useQueryClient();
  const [companyDraft, setCompanyDraft] = useState({ name: "VCOS Company", slug: "vcos-company" });
  const [initDraft, setInitDraft] = useState<ChannelInitDraft | null>(null);
  const [contractDraft, setContractDraft] = useState<ChannelContractDraft | null>(null);
  const [compileResult, setCompileResult] = useState<ChannelInitCompileResult | null>(null);
  const form = useForm<MinimalFormValues>({
    resolver: zodResolver(minimalSchema),
    defaultValues: {
      company_id: "",
      public_presence_mode: "EXISTING_PUBLIC_CHANNEL",
      youtube_url_or_handle: "",
      website_url: "",
      social_profile_links: "",
      operator_note_purpose: "",
      intended_content_language: "",
      intended_primary_market: "",
      owner_operator_language: "vi-VN",
      initial_topic_pillar_hints: "",
      source_usage_attestation: false
    }
  });
  const companiesQuery = useQuery({ queryKey: queryKeys.companies, queryFn: getCompanies });
  const companyMutation = useMutation({
    mutationFn: createCompany,
    onSuccess: async (company) => {
      form.setValue("company_id", company.id, { shouldDirty: true, shouldValidate: true });
      setCompanyDraft({ name: "", slug: "" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.companies });
    }
  });
  const companies = companiesQuery.data?.length ? companiesQuery.data : companyMutation.data ? [companyMutation.data] : [];
  const selectedCompanyId = form.watch("company_id");
  useEffect(() => {
    const fetchedCompanies = companiesQuery.data ?? [];
    if (fetchedCompanies.length === 1 && !selectedCompanyId) {
      form.setValue("company_id", fetchedCompanies[0].id, { shouldValidate: true });
    }
  }, [companiesQuery.data, form, selectedCompanyId]);

  const researchMutation = useMutation({
    mutationFn: async (submitted: MinimalFormValues) => {
      const draft = await createChannelInitDraft({
        company_id: submitted.company_id,
        channel_name: submitted.channel_name,
        public_presence_mode: submitted.public_presence_mode,
        youtube_url_or_handle: submitted.youtube_url_or_handle || null,
        website_url: submitted.website_url || null,
        social_profile_links: splitLines(submitted.social_profile_links),
        operator_note_purpose: submitted.operator_note_purpose,
        intended_content_language: submitted.intended_content_language || null,
        intended_primary_market: submitted.intended_primary_market || null,
        owner_operator_language: submitted.owner_operator_language || "vi-VN",
        initial_topic_pillar_hints: splitLines(submitted.initial_topic_pillar_hints),
        source_usage_attestation: submitted.source_usage_attestation
      });
      const researched = await researchChannelInitDraft(draft.id);
      return { draft, researched };
    },
    onSuccess: ({ draft, researched }) => {
      setInitDraft(draft);
      setContractDraft(researched);
      setCompileResult(null);
    }
  });
  const reviewMutation = useMutation({
    mutationFn: async () => {
      if (!initDraft || !contractDraft) throw new Error("Chưa có draft để review.");
      const decisions = reviewPaths
        .filter((item) => contractDraft.field_source_map_json[item.path])
        .map((item) => ({ field_path: item.path, action: "confirm", note: "Operator confirmed in minimal review step." }));
      return reviewChannelInitDraft(initDraft.id, decisions, "Xác nhận các field chiến lược trước compile.");
    },
    onSuccess: (reviewed) => {
      setContractDraft(reviewed);
      setCompileResult(null);
    }
  });
  const compileMutation = useMutation({
    mutationFn: async () => {
      if (!initDraft) throw new Error("Chưa có init draft.");
      return compileChannelInitDraft(initDraft.id);
    },
    onSuccess: async (compiled) => {
      setCompileResult(compiled);
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });
  const activateMutation = useMutation({
    mutationFn: async () => {
      if (!compileResult) throw new Error("Chưa có snapshot COMPLETE.");
      return activateChannel(compileResult.channel_id, compileResult.compiled_policy_snapshot_id);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });
  const canActivate = compileResult?.contract_status === "COMPLETE";

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Tạo kênh"
        subtitle="Dữ liệu khởi tạo kênh là nguồn sự thật vận hành sau khi người vận hành xác nhận."
        breadcrumbs={[{ label: "Kênh", href: "/channels" }, { label: "Tạo kênh" }]}
      />

      <Panel className="border-primary/30">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1 text-sm text-muted-foreground">
            <p>Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime.</p>
            <p>VCOS không tự publish/upload/reupload.</p>
            <p>Không dùng YouTube Studio scraping.</p>
            <p>Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.</p>
          </div>
          <Button type="button" onClick={onAdvanced}>
            <Settings2 size={16} />
            Nâng cao: nhập thủ công toàn bộ hồ sơ
          </Button>
        </div>
      </Panel>

      <form className="space-y-5" onSubmit={form.handleSubmit((submitted) => researchMutation.mutate(submitted))}>
        <Section title="1. Thiết lập tối thiểu">
          <CompanySelect
            companies={companies}
            loading={companiesQuery.isLoading}
            error={form.formState.errors.company_id?.message}
            registration={form.register("company_id")}
          />
          {!companiesQuery.isLoading && companies.length === 0 ? (
            <CompanyBootstrapInline
              draft={companyDraft}
              onDraftChange={setCompanyDraft}
              onCreate={() => companyMutation.mutate(companyDraft)}
              pending={companyMutation.isPending}
              error={companyMutation.isError ? companyMutation.error.message : null}
            />
          ) : null}
          <TextInput label="Tên kênh *" error={form.formState.errors.channel_name?.message} registration={form.register("channel_name")} />
          <SelectInput
            label="Public presence mode *"
            registration={form.register("public_presence_mode")}
            options={[
              ["EXISTING_PUBLIC_CHANNEL", "Kênh đã có footprint công khai"],
              ["NEW_CHANNEL_NO_PUBLIC_FOOTPRINT", "Kênh mới chưa có footprint công khai"]
            ]}
          />
          <TextInput label="YouTube URL/handle" error={form.formState.errors.youtube_url_or_handle?.message} registration={form.register("youtube_url_or_handle")} />
          <TextInput label="Website URL" error={form.formState.errors.website_url?.message} registration={form.register("website_url")} />
          <TextArea label="Social/profile links" error={form.formState.errors.social_profile_links?.message} registration={form.register("social_profile_links")} />
          <TextArea label="Ghi chú ngắn của operator *" error={form.formState.errors.operator_note_purpose?.message} registration={form.register("operator_note_purpose")} />
          <TextInput label="Content language" error={form.formState.errors.intended_content_language?.message} registration={form.register("intended_content_language")} />
          <TextInput label="Primary market" error={form.formState.errors.intended_primary_market?.message} registration={form.register("intended_primary_market")} />
          <TextInput label="Owner/operator language" error={form.formState.errors.owner_operator_language?.message} registration={form.register("owner_operator_language")} />
          <TextArea label="Initial topic/pillar hints" error={form.formState.errors.initial_topic_pillar_hints?.message} registration={form.register("initial_topic_pillar_hints")} />
          <Checkbox label="Tôi xác nhận các URL này là nguồn công khai hoặc được phép dùng để research hồ sơ kênh." registration={form.register("source_usage_attestation")} />
          {form.formState.errors.source_usage_attestation?.message ? <div className="text-sm text-rose-100">{form.formState.errors.source_usage_attestation.message}</div> : null}
        </Section>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={researchMutation.isPending || companiesQuery.isLoading || !selectedCompanyId}>
            <Search size={16} />
            {researchMutation.isPending ? "Đang tạo nháp..." : "Tạo nháp & research hồ sơ kênh"}
          </Button>
        </div>
      </form>

      {researchMutation.isError ? <ErrorState message={researchMutation.error.message} /> : null}
      {reviewMutation.isError ? <ErrorState message={reviewMutation.error.message} /> : null}
      {compileMutation.isError ? <ErrorState message={compileMutation.error.message} /> : null}
      {activateMutation.isError ? <ErrorState message={activateMutation.error.message} /> : null}

      {contractDraft ? (
        <>
          <ResearchResultPanel contractDraft={contractDraft} />
          <ReviewPanel contractDraft={contractDraft} onConfirm={() => reviewMutation.mutate()} pending={reviewMutation.isPending} />
          <CompilePanel result={compileResult} contractDraft={contractDraft} onCompile={() => compileMutation.mutate()} pending={compileMutation.isPending} />
        </>
      ) : null}

      {canActivate ? (
        <Panel className="border-emerald-500/40">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">5. Activate</h2>
              <p className="mt-1 text-sm text-muted-foreground">Chỉ kích hoạt sau khi Channel Contract COMPILED_COMPLETE và người vận hành bấm rõ ràng.</p>
            </div>
            <Button type="button" onClick={() => activateMutation.mutate()} disabled={activateMutation.isPending}>
              <ShieldCheck size={16} />
              Kích hoạt kênh
            </Button>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function AdvancedManualChannelInitForm() {
  const queryClient = useQueryClient();
  const [companyDraft, setCompanyDraft] = useState({ name: "VCOS Company", slug: "vcos-company" });
  const form = useForm<FormValues>({
    resolver: zodResolver(advancedSchema),
    defaultValues: {
      company_id: "",
      template_key: "saas_digital_leverage",
      channel_type: "YOUTUBE_CHANNEL",
      secondary_platforms: "Shorts",
      audience_level: "semi_technical",
      operator_language: "vi",
      measurement_units: "metric",
      date_format: "DD/MM/YYYY",
      market_examples_preference: "prefer",
      long_form_enabled: true,
      long_form_min_minutes: 8,
      long_form_max_minutes: 14,
      shorts_enabled: true,
      shorts_min_seconds: 30,
      shorts_max_seconds: 45,
      shorts_hard_max_seconds: 59,
      captions_required: true,
      chapters_required_for_long_form: true,
      derivative_shorts_per_long_form: 2,
      narration_tone: "practical_explainer",
      pacing: "clear_short_sentences",
      forbidden_style: "hype\nfearmongering\naggressive_sales\nfake_urgency",
      cost_sensitivity: "medium",
      avoid_unnecessary_ai_hero: true,
      prefer_reuse_safe_assets: true,
      exact_cost_claim_requires_provider_snapshot: true,
      reused_content_sensitivity: "medium",
      drive_offload_enabled: true
    }
  });
  const companiesQuery = useQuery({ queryKey: queryKeys.companies, queryFn: getCompanies });
  const companyMutation = useMutation({
    mutationFn: createCompany,
    onSuccess: async (company) => {
      form.setValue("company_id", company.id, { shouldDirty: true, shouldValidate: true });
      setCompanyDraft({ name: "", slug: "" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.companies });
    }
  });
  const companies = companiesQuery.data?.length ? companiesQuery.data : companyMutation.data ? [companyMutation.data] : [];
  const values = form.watch();
  const selectedCompanyId = values.company_id;
  const preview = contractPreview(values);
  const mutation = useMutation({
    mutationFn: initChannel,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });
  const compiled = mutation.data?.compiled as Record<string, unknown> | undefined;
  const compiledContract = compiled?.channel_contract_json as Record<string, unknown> | undefined;
  const compiledStatus = String(compiled?.contract_status ?? compiledContract?.contract_status ?? "");
  const activateMutation = useMutation({
    mutationFn: () => activateChannel(String(mutation.data?.channel.id), String(compiled?.id ?? compiled?.snapshot_id ?? "")),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });
  const canActivate = mutation.isSuccess && compiledStatus === "COMPLETE";
  const channelSubmitDisabled = mutation.isPending || companiesQuery.isLoading || !selectedCompanyId;

  useEffect(() => {
    const fetchedCompanies = companiesQuery.data ?? [];
    if (fetchedCompanies.length === 1 && !selectedCompanyId) {
      form.setValue("company_id", fetchedCompanies[0].id, { shouldValidate: true });
    }
  }, [companiesQuery.data, form, selectedCompanyId]);

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Tạo kênh"
        subtitle="Channel initialization data là production truth. Người vận hành nhập cấu hình, agent không tự đoán."
        breadcrumbs={[{ label: "Kênh", href: "/channels" }, { label: "Tạo kênh" }]}
      />

      <Panel className="border-primary/30">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Trạng thái hồ sơ</h2>
            <p className="mt-1 text-sm text-muted-foreground">Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.</p>
            <p className="mt-1 text-sm text-muted-foreground">VCOS không tự publish/upload/reupload.</p>
          </div>
          <StatusBadge value={preview.status === "COMPLETE" ? "Hồ sơ đủ để kích hoạt" : "Thiếu thông tin cấu hình"} />
        </div>
        {preview.missing.length ? (
          <div className="mt-4 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
            Thiếu: {preview.missing.join(", ")}
          </div>
        ) : null}
      </Panel>

      <form className="space-y-5" onSubmit={form.handleSubmit((submitted) => mutation.mutate(submitted))}>
        <Section title="Thông tin kênh">
          <CompanySelect
            companies={companies}
            loading={companiesQuery.isLoading}
            error={form.formState.errors.company_id?.message}
            registration={form.register("company_id")}
          />
          {!companiesQuery.isLoading && companies.length === 0 ? (
            <CompanyBootstrapInline
              draft={companyDraft}
              onDraftChange={setCompanyDraft}
              onCreate={() => companyMutation.mutate(companyDraft)}
              pending={companyMutation.isPending}
              error={companyMutation.isError ? companyMutation.error.message : null}
            />
          ) : null}
          {companiesQuery.isError ? <div className="text-sm text-rose-100">Không tải được danh sách công ty: {companiesQuery.error.message}</div> : null}
          <TextInput label="Khóa kênh *" error={form.formState.errors.key?.message} registration={form.register("key")} />
          <TextInput label="Tên kênh *" error={form.formState.errors.name?.message} registration={form.register("name")} />
          <TextInput label="Template hồ sơ *" error={form.formState.errors.template_key?.message} registration={form.register("template_key")} />
          <TextInput label="Loại kênh *" error={form.formState.errors.channel_type?.message} registration={form.register("channel_type")} />
          <TextInput label="Niche / chủ đề chính *" error={form.formState.errors.niche?.message} registration={form.register("niche")} />
          <TextInput label="Định vị kênh *" error={form.formState.errors.positioning?.message} registration={form.register("positioning")} />
          <TextInput label="Brand promise / lời hứa nội dung *" error={form.formState.errors.brand_promise?.message} registration={form.register("brand_promise")} />
          <ReadOnly label="Platform chính" value="YouTube" />
          <TextInput label="Platform phụ" error={form.formState.errors.secondary_platforms?.message} registration={form.register("secondary_platforms")} />
        </Section>

        <Section title="Đối tượng người xem">
          <TextInput label="Persona chính *" error={form.formState.errors.primary_persona?.message} registration={form.register("primary_persona")} />
          <SelectInput label="Mức độ hiểu biết *" registration={form.register("audience_level")} options={[["non_technical", "Không kỹ thuật"], ["semi_technical", "Có hiểu biết cơ bản"], ["technical", "Kỹ thuật"], ["expert", "Chuyên gia"]]} />
          <TextArea label="Pain points *" error={form.formState.errors.pain_points?.message} registration={form.register("pain_points")} />
          <TextArea label="Desired outcome *" error={form.formState.errors.desired_outcome?.message} registration={form.register("desired_outcome")} />
          <TextArea label="Audience notes" error={form.formState.errors.audience_notes?.message} registration={form.register("audience_notes")} />
        </Section>

        <Section title="Thị trường & locale">
          <SelectInput label="Primary market *" registration={form.register("primary_market")} options={marketOptions} placeholder="Chọn thị trường" error={form.formState.errors.primary_market?.message} />
          <TextInput label="Secondary markets" error={form.formState.errors.secondary_markets?.message} registration={form.register("secondary_markets")} />
          <SelectInput label="Audience locale *" registration={form.register("audience_locale")} options={localeOptions} placeholder="Chọn locale" error={form.formState.errors.audience_locale?.message} />
          <TextInput label="Content language *" error={form.formState.errors.content_language?.message} registration={form.register("content_language")} />
          <TextInput label="Operator language *" error={form.formState.errors.operator_language?.message} registration={form.register("operator_language")} />
          <TextInput label="Timezone *" error={form.formState.errors.timezone?.message} registration={form.register("timezone")} />
          <TextInput label="Currency *" error={form.formState.errors.currency?.message} registration={form.register("currency")} />
          <SelectInput label="Measurement units *" registration={form.register("measurement_units")} options={[["metric", "Metric"], ["imperial", "Imperial"], ["imperial_or_mixed", "Imperial hoặc mixed"]]} />
          <SelectInput label="Date format *" registration={form.register("date_format")} options={dateFormatOptions} />
          <TextInput label="Tone văn hóa *" error={form.formState.errors.cultural_tone?.message} registration={form.register("cultural_tone")} />
          <TextInput label="Formality *" error={form.formState.errors.cultural_formality?.message} registration={form.register("cultural_formality")} />
          <TextInput label="Humor *" error={form.formState.errors.cultural_humor?.message} registration={form.register("cultural_humor")} />
          <TextInput label="CTA style *" error={form.formState.errors.cta_style?.message} registration={form.register("cta_style")} />
          <SelectInput label="Market examples preference *" registration={form.register("market_examples_preference")} options={[["prefer", "Ưu tiên"], ["avoid", "Tránh"]]} />
          <TextInput label="Finance claim sensitivity *" error={form.formState.errors.finance_claim_sensitivity?.message} registration={form.register("finance_claim_sensitivity")} />
          <TextInput label="Health claim sensitivity *" error={form.formState.errors.health_claim_sensitivity?.message} registration={form.register("health_claim_sensitivity")} />
          <TextInput label="Disclosure standard *" error={form.formState.errors.disclosure_standard?.message} registration={form.register("disclosure_standard")} />
        </Section>

        <Section title="Editorial strategy">
          <TextArea label="Content pillars *" error={form.formState.errors.content_pillars?.message} registration={form.register("content_pillars")} />
          <TextArea label="Allowed angles *" error={form.formState.errors.allowed_angles?.message} registration={form.register("allowed_angles")} />
          <TextArea label="Forbidden angles *" error={form.formState.errors.forbidden_angles?.message} registration={form.register("forbidden_angles")} />
          <ReadOnly label="Claim style" value="Measured, evidence-backed, no exaggerated ROI" />
          <TextArea label="Allowed topics *" error={form.formState.errors.allowed_topics?.message} registration={form.register("allowed_topics")} />
          <TextArea label="Forbidden topics *" error={form.formState.errors.forbidden_topics?.message} registration={form.register("forbidden_topics")} />
        </Section>

        <Section title="Format policy">
          <Checkbox label="Long-form enabled" registration={form.register("long_form_enabled")} />
          <NumberInput label="Long-form min phút" error={form.formState.errors.long_form_min_minutes?.message} registration={form.register("long_form_min_minutes")} />
          <NumberInput label="Long-form max phút" error={form.formState.errors.long_form_max_minutes?.message} registration={form.register("long_form_max_minutes")} />
          <ReadOnly label="Long-form structure" value="Hook, problem, mechanism, result, takeaway" />
          <Checkbox label="Shorts enabled" registration={form.register("shorts_enabled")} />
          <NumberInput label="Shorts min giây" error={form.formState.errors.shorts_min_seconds?.message} registration={form.register("shorts_min_seconds")} />
          <NumberInput label="Shorts max giây" error={form.formState.errors.shorts_max_seconds?.message} registration={form.register("shorts_max_seconds")} />
          <NumberInput label="Shorts hard max seconds" error={form.formState.errors.shorts_hard_max_seconds?.message} registration={form.register("shorts_hard_max_seconds")} />
          <Checkbox label="Captions required" registration={form.register("captions_required")} />
          <Checkbox label="Chapters required for long-form" registration={form.register("chapters_required_for_long_form")} />
          <NumberInput label="Số Shorts phải sinh mỗi video dài" error={form.formState.errors.derivative_shorts_per_long_form?.message} registration={form.register("derivative_shorts_per_long_form")} />
        </Section>

        <Section title="Voice / tone style">
          <SelectInput label="Narration tone *" registration={form.register("narration_tone")} options={[["documentary_explainer", "Documentary explainer"], ["practical_explainer", "Practical explainer"], ["calm_professional", "Calm professional"], ["investigative", "Investigative"]]} />
          <SelectInput label="Pacing *" registration={form.register("pacing")} options={[["clear_short_sentences", "Câu ngắn, rõ"], ["moderate", "Vừa phải"], ["fast", "Nhanh"]]} />
          <TextArea label="Allowed style *" error={form.formState.errors.allowed_style?.message} registration={form.register("allowed_style")} />
          <TextArea label="Forbidden style *" error={form.formState.errors.forbidden_style?.message} registration={form.register("forbidden_style")} />
        </Section>

        <Section title="Platform strategy">
          <ReadOnly label="Primary platform" value="YouTube" />
          <ReadOnly label="YouTube is learning authority" value="True" />
          <ReadOnly label="Publish mode" value="Human handoff only" />
          <ReadOnly label="Auto publish allowed" value="False" />
          <ReadOnly label="Studio scraping allowed" value="False" />
          <ReadOnly label="Disabled authorities" value="TikTok analytics learning, Facebook analytics learning" />
        </Section>

        <Section title="Media/provider policy">
          <ReadOnly label="Voice provider" value="ElevenLabs" />
          <ReadOnly label="AI hero provider" value="Google Vertex Veo" />
          <ReadOnly label="AI hero model ID" value="veo-3.1-fast-generate-001" />
          <ReadOnly label="AI hero allowed durations" value="4, 6, 8 giây" />
          <ReadOnly label="AI hero default duration" value="8 giây" />
          <ReadOnly label="AI hero audio" value="False" />
          <ReadOnly label="AI hero allowed use" value="Hero shot, hard-to-find visual" />
          <ReadOnly label="AI hero forbidden use" value="Data diagram, workflow chart, factual evidence visualization" />
          <ReadOnly label="Renderer" value="Creatomate Growth 10K" />
          <ReadOnly label="Storage archive" value="Google Drive" />
          <Checkbox label="Drive offload enabled" registration={form.register("drive_offload_enabled")} />
        </Section>

        <Section title="Rights / disclosure policy">
          <ReadOnly label="Source manifest required" value="True" />
          <ReadOnly label="Rights evidence required" value="True" />
          <ReadOnly label="AI disclosure required when AI media used" value="True" />
          <ReadOnly label="Synthetic media warning when applicable" value="True" />
          <ReadOnly label="Music policy" value="Approved/licensed/audio-library-safe only" />
          <SelectInput label="Reused content sensitivity *" registration={form.register("reused_content_sensitivity")} options={[["low", "Thấp"], ["medium", "Vừa"], ["high", "Cao"]]} />
        </Section>

        <Section title="Cost policy">
          <SelectInput label="Cost sensitivity *" registration={form.register("cost_sensitivity")} options={[["low", "Thấp"], ["medium", "Vừa"], ["high", "Cao"]]} />
          <Checkbox label="Avoid unnecessary AI hero" registration={form.register("avoid_unnecessary_ai_hero")} />
          <Checkbox label="Prefer reuse safe assets" registration={form.register("prefer_reuse_safe_assets")} />
          <Checkbox label="Exact cost claim requires provider snapshot" registration={form.register("exact_cost_claim_requires_provider_snapshot")} />
        </Section>

        <Section title="Learning policy">
          <ReadOnly label="Authority" value="YouTube analytics only" />
          <TextInput label="Min evidence required *" error={form.formState.errors.min_evidence_required?.message} registration={form.register("min_evidence_required")} />
          <ReadOnly label="Auto-promote learning" value="False" />
          <ReadOnly label="Config mutation by agent allowed" value="False" />
          <ReadOnly label="Weak evidence action" value="Summarize limitations only" />
        </Section>

        <Section title="Forbidden behavior">
          {["fake_traffic", "bot_engagement", "spam_reupload", "algorithm_manipulation", "platform_evasion", "ip_vps_tricks", "youtube_studio_scraping", "dashboard_scraping", "invented_metrics", "invented_sources", "invented_rights", "unsupported_local_claims"].map((item) => (
            <ReadOnly key={item} label={item} value="Locked forbidden" />
          ))}
        </Section>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={channelSubmitDisabled}>
            <CheckCircle2 size={16} />
            Tạo và compile snapshot
          </Button>
          {canActivate ? (
            <Button type="button" onClick={() => activateMutation.mutate()} disabled={activateMutation.isPending}>
              <ShieldCheck size={16} />
              Kích hoạt kênh
            </Button>
          ) : null}
          <StatusBadge value={preview.status === "COMPLETE" ? "Hồ sơ đủ để kích hoạt" : "Thiếu thông tin cấu hình"} />
        </div>
      </form>

      {mutation.isError ? <ErrorState message={mutation.error.message} /> : null}
      {activateMutation.isError ? <ErrorState message={activateMutation.error.message} /> : null}
      {mutation.isSuccess ? (
        <Panel className="border-emerald-500/40">
          <h2 className="text-base font-semibold">{compiledStatus === "COMPLETE" ? "Hồ sơ đủ để kích hoạt" : "Cần bổ sung hồ sơ"}</h2>
          <p className="mt-2 text-sm text-muted-foreground">Snapshot đã compile. Kênh chỉ được kích hoạt khi Channel Contract COMPLETE.</p>
        </Panel>
      ) : null}
    </div>
  );
}

function ResearchResultPanel({ contractDraft }: { contractDraft: ChannelContractDraft }) {
  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">2. Kết quả research</h2>
          <p className="mt-1 text-sm text-muted-foreground">Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime.</p>
        </div>
        <StatusBadge value={contractDraft.contract_status ?? "PARTIAL"} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="rounded-md border border-border p-3">
          <h3 className="text-sm font-semibold">Summary</h3>
          <p className="mt-2 text-sm text-muted-foreground">{String(getPath(contractDraft.suggested_channel_contract, "channel_identity.positioning") ?? "Chưa có đề xuất.")}</p>
        </div>
        <div className="rounded-md border border-border p-3">
          <h3 className="text-sm font-semibold">Evidence refs</h3>
          <div className="mt-2 space-y-2 text-sm text-muted-foreground">
            {contractDraft.evidence_refs.map((item) => (
              <div key={item.ref_id}>
                <span className="font-medium text-foreground">{item.ref_id}</span> · {item.source_type} · {item.reliability}
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-md border border-border p-3">
          <h3 className="text-sm font-semibold">Cannot determine fields</h3>
          <div className="mt-2 space-y-1 text-sm text-muted-foreground">
            {contractDraft.missing_fields.length ? contractDraft.missing_fields.slice(0, 8).map((field) => <div key={field}>{field}</div>) : <div>Chưa có blocker.</div>}
          </div>
        </div>
      </div>
      {contractDraft.risks.length ? (
        <div className="mt-4 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
          {contractDraft.risks.map((risk) => String(risk.message_vi ?? risk.risk_code)).join(" · ")}
        </div>
      ) : null}
    </Panel>
  );
}

function ReviewPanel({ contractDraft, onConfirm, pending }: { contractDraft: ChannelContractDraft; onConfirm: () => void; pending: boolean }) {
  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">3. Người vận hành rà soát</h2>
          <p className="mt-1 text-sm text-muted-foreground">Research inference chưa đủ để COMPLETE. Người vận hành cần xác nhận field chiến lược.</p>
        </div>
        <Button type="button" onClick={onConfirm} disabled={pending}>
          <CheckCircle2 size={16} />
          {pending ? "Đang xác nhận..." : "Xác nhận các field bắt buộc"}
        </Button>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {reviewPaths.map((item) => (
          <FieldReviewRow
            key={item.path}
            label={item.label}
            path={item.path}
            value={getPath(contractDraft.suggested_channel_contract, item.path)}
            meta={contractDraft.field_source_map_json[item.path]}
          />
        ))}
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <LockedPolicy title="Provider policy" items={["media_policy.voice_provider", "media_policy.ai_hero_provider", "media_policy.renderer"]} fieldMap={contractDraft.field_source_map_json} contract={contractDraft.suggested_channel_contract} />
        <LockedPolicy title="Safety policy" items={["platform_strategy.auto_publish_allowed", "platform_strategy.studio_scraping_allowed", "learning_policy.config_mutation_by_agent_allowed"]} fieldMap={contractDraft.field_source_map_json} contract={contractDraft.suggested_channel_contract} />
        <LockedPolicy title="Publish policy" items={["platform_strategy.publish_mode", "forbidden_behavior"]} fieldMap={contractDraft.field_source_map_json} contract={contractDraft.suggested_channel_contract} />
      </div>
    </Panel>
  );
}

function CompilePanel({
  result,
  contractDraft,
  onCompile,
  pending
}: {
  result: ChannelInitCompileResult | null;
  contractDraft: ChannelContractDraft;
  onCompile: () => void;
  pending: boolean;
}) {
  const status = result?.contract_status ?? contractDraft.contract_status ?? "PARTIAL";
  const missing = result?.missing_fields ?? contractDraft.missing_fields;
  const fieldMap = result?.field_source_map_json ?? contractDraft.field_source_map_json;
  const contract = result?.channel_contract_json ?? contractDraft.suggested_channel_contract;
  const leafCount = Object.keys(flattenLeafPaths(contract)).length;
  const coveredCount = Object.keys(flattenLeafPaths(contract)).filter((path) => fieldMap[path]).length;
  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">4. Compile Channel Contract</h2>
          <p className="mt-1 text-sm text-muted-foreground">channel_contract_json giữ canonical values; provenance nằm trong field_source_map_json.</p>
        </div>
        <Button type="button" onClick={onCompile} disabled={pending}>
          <CheckCircle2 size={16} />
          {pending ? "Đang compile..." : "Compile Channel Contract"}
        </Button>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <ReadOnly label="Contract status" value={status} />
        <ReadOnly label="Field source coverage" value={`${coveredCount}/${leafCount}`} />
        <ReadOnly label="Activation eligibility" value={result?.activation_eligibility ? "Có" : "Chưa"} />
      </div>
      {missing.length ? (
        <div className="mt-4 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
          Missing: {missing.slice(0, 12).join(", ")}
        </div>
      ) : null}
      {result?.contradiction_reasons.length ? (
        <div className="mt-4 rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">
          Contradictory: {result.contradiction_reasons.join(", ")}
        </div>
      ) : null}
    </Panel>
  );
}

function FieldReviewRow({ label, path, value, meta }: { label: string; path: string; value: unknown; meta?: FieldMeta }) {
  return (
    <div className="rounded-md border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium">{label}</div>
        <FieldSourceBadge meta={meta} />
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{path}</div>
      <div className="mt-2 break-words text-muted-foreground">{formatValue(value)}</div>
      <div className="mt-2 text-xs text-muted-foreground">Confidence: {meta?.confidence_label ?? "LOW"}</div>
    </div>
  );
}

function LockedPolicy({
  title,
  items,
  fieldMap,
  contract
}: {
  title: string;
  items: string[];
  fieldMap: Record<string, FieldMeta>;
  contract: Record<string, unknown>;
}) {
  return (
    <div className="rounded-md border border-border p-3 text-sm">
      <h3 className="font-semibold">{title}</h3>
      <div className="mt-2 space-y-2">
        {items.map((path) => (
          <div key={path} className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">{path}</span>
            <FieldSourceBadge meta={fieldMap[path]} fallbackValue={formatValue(getPath(contract, path))} />
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldSourceBadge({ meta, fallbackValue }: { meta?: FieldMeta; fallbackValue?: string }) {
  const label = sourceTypeLabel(meta?.source_type);
  return <StatusBadge value={label || fallbackValue || "Không xác định"} />;
}

const reviewPaths = [
  { path: "market_locale.primary_market", label: "Market" },
  { path: "market_locale.audience_locale", label: "Locale" },
  { path: "target_audience.primary_persona", label: "Audience" },
  { path: "channel_identity.niche", label: "Niche" },
  { path: "channel_identity.positioning", label: "Positioning" },
  { path: "editorial_strategy.content_pillars", label: "Content pillars" },
  { path: "editorial_strategy.claim_style", label: "Claim style" },
  { path: "format_policy.long_form.enabled", label: "Long-form policy" },
  { path: "format_policy.shorts.enabled", label: "Shorts policy" },
  { path: "rights_policy.source_manifest_required", label: "Rights/disclosure" },
  { path: "learning_policy.min_evidence_required", label: "Learning policy" }
];

function sourceTypeLabel(sourceType?: string) {
  const labels: Record<string, string> = {
    ADMIN_INPUT: "Người vận hành nhập",
    ADMIN_HINT: "Gợi ý từ research",
    PUBLIC_RESEARCH_EVIDENCE: "Gợi ý từ research",
    RESEARCH_INFERENCE: "Cần xác nhận",
    HUMAN_CONFIRMED: "Người vận hành nhập",
    GLOBAL_LOCKED_POLICY: "Chính sách hệ thống khóa",
    PROVIDER_POLICY: "Nhà cung cấp khóa",
    COMPILER_DERIVED: "Chính sách hệ thống khóa",
    UNKNOWN: "Không xác định"
  };
  return sourceType ? labels[sourceType] ?? "Không xác định" : "Không xác định";
}

function getPath(payload: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (current && typeof current === "object" && key in current) {
      return (current as Record<string, unknown>)[key];
    }
    return undefined;
  }, payload);
}

function flattenLeafPaths(value: unknown, prefix = ""): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>((acc, [key, child]) => {
      return { ...acc, ...flattenLeafPaths(child, prefix ? `${prefix}.${key}` : key) };
    }, {});
  }
  return prefix ? { [prefix]: value } : {};
}

function formatValue(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => (typeof item === "object" ? JSON.stringify(item) : String(item))).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  if (value === undefined || value === null || value === "") return "Không xác định";
  return String(value);
}

function splitLines(value: string | undefined | null): string[] {
  return String(value ?? "")
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

const marketOptions = [["US", "US"], ["UK", "UK"], ["EU", "EU"], ["JP", "JP"], ["KR", "KR"], ["VN", "VN"], ["AU", "AU"], ["CA", "CA"], ["OTHER", "OTHER"]];
const localeOptions = [["en-US", "English US"], ["en-GB", "English UK"], ["ja-JP", "Japanese"], ["ko-KR", "Korean"], ["vi-VN", "Tiếng Việt"], ["other", "Other"]];
const dateFormatOptions = [["MM/DD/YYYY", "MM/DD/YYYY"], ["DD/MM/YYYY", "DD/MM/YYYY"], ["YYYY/MM/DD", "YYYY/MM/DD"], ["YYYY.MM.DD", "YYYY.MM.DD"]];

function contractPreview(values: Partial<FormValues>) {
  const missing: string[] = [];
  for (const [key, label] of [
    ["company_id", "Công ty"],
    ["name", "Tên kênh"],
    ["niche", "Niche"],
    ["primary_persona", "Persona"],
    ["primary_market", "Primary market"],
    ["audience_locale", "Audience locale"],
    ["content_language", "Content language"],
    ["timezone", "Timezone"],
    ["content_pillars", "Content pillars"],
    ["narration_tone", "Narration tone"]
  ] as Array<[keyof FormValues, string]>) {
    if (!values[key]) missing.push(label);
  }
  return { status: missing.length ? "PARTIAL" : "COMPLETE", missing };
}

function CompanySelect({
  companies,
  loading,
  error,
  registration
}: {
  companies: Company[];
  loading: boolean;
  error?: string;
  registration: UseFormRegisterReturn;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">Công ty *</span>
      <select className="w-full rounded-md border border-border bg-background px-3 py-2" disabled={loading || companies.length === 0} {...registration}>
        <option value="">{loading ? "Đang tải công ty" : "Chọn công ty"}</option>
        {companies.map((company) => (
          <option key={company.id} value={company.id}>{company.name} ({company.slug})</option>
        ))}
      </select>
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}

function CompanyBootstrapInline({
  draft,
  onDraftChange,
  onCreate,
  pending,
  error
}: {
  draft: { name: string; slug: string };
  onDraftChange: (draft: { name: string; slug: string }) => void;
  onCreate: () => void;
  pending: boolean;
  error: string | null;
}) {
  const canCreate = draft.name.trim() && draft.slug.trim();
  return (
    <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm xl:col-span-2">
      <h3 className="font-semibold text-amber-100">Tạo công ty trước</h3>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="block">
          <span className="mb-2 block text-muted-foreground">Tên công ty</span>
          <input
            className="w-full rounded-md border border-border bg-background px-3 py-2"
            value={draft.name}
            onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
          />
        </label>
        <label className="block">
          <span className="mb-2 block text-muted-foreground">Slug công ty</span>
          <input
            className="w-full rounded-md border border-border bg-background px-3 py-2"
            value={draft.slug}
            onChange={(event) => onDraftChange({ ...draft, slug: event.target.value })}
          />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={onCreate} disabled={pending || !canCreate}>
          {pending ? "Đang tạo..." : "Tạo công ty"}
        </Button>
        <span className="text-xs text-muted-foreground">Company là dữ liệu setup do operator nhập, không phải nội dung do AI tạo.</span>
      </div>
      {error ? <div className="mt-2 text-xs text-rose-100">{error}</div> : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Panel>
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">{children}</div>
    </Panel>
  );
}

function TextInput({ label, error, registration }: { label: string; error?: string; registration: UseFormRegisterReturn }) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">{label}</span>
      <input className="w-full rounded-md border border-border bg-background px-3 py-2" {...registration} />
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}

function NumberInput({ label, error, registration }: { label: string; error?: string; registration: UseFormRegisterReturn }) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">{label}</span>
      <input type="number" className="w-full rounded-md border border-border bg-background px-3 py-2" {...registration} />
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}

function TextArea({ label, error, registration }: { label: string; error?: string; registration: UseFormRegisterReturn }) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">{label}</span>
      <textarea className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2" {...registration} />
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}

function SelectInput({
  label,
  error,
  registration,
  options,
  placeholder
}: {
  label: string;
  error?: string;
  registration: UseFormRegisterReturn;
  options: string[][];
  placeholder?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-2 block text-muted-foreground">{label}</span>
      <select className="w-full rounded-md border border-border bg-background px-3 py-2" {...registration}>
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select>
      {error ? <span className="mt-1 block text-xs text-rose-100">{error}</span> : null}
    </label>
  );
}

function Checkbox({ label, registration }: { label: string; registration: UseFormRegisterReturn }) {
  return (
    <label className="flex min-h-10 items-center gap-3 rounded-md border border-border px-3 py-2 text-sm">
      <input type="checkbox" {...registration} />
      {label}
    </label>
  );
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-1 font-medium">{value}</div>
    </div>
  );
}
