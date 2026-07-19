"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileSearch, PencilLine, ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/cockpit";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  approveTargetMarketDraft,
  createCompany,
  createMinimalMarketChannel,
  getCompanies,
  queryKeys,
  runTargetMarketDraft,
  updateTargetMarketDraft
} from "@/lib/api";
import type { TargetMarketProfileDraft } from "@/lib/types";

type InitForm = {
  company_id: string;
  channel_name: string;
  channel_key: string;
  channel_purpose: string;
  target_audience_summary: string;
  primary_market: string;
  primary_language: string;
  primary_locale: string;
  channel_market_type: "MARKET_NATIVE" | "GLOBAL_ENGLISH";
  known_destination_channel: string;
  account_country: string;
};

const defaultForm: InitForm = {
  company_id: "",
  channel_name: "",
  channel_key: "",
  channel_purpose: "",
  target_audience_summary: "",
  primary_market: "US",
  primary_language: "en",
  primary_locale: "en-US",
  channel_market_type: "MARKET_NATIVE",
  known_destination_channel: "",
  account_country: ""
};

export function ChannelInitWizard() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(defaultForm);
  const [companyDraft, setCompanyDraft] = useState({ name: "VCOS Company", slug: "vcos-company" });
  const [channelId, setChannelId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TargetMarketProfileDraft | null>(null);
  const [approvedHash, setApprovedHash] = useState<string | null>(null);
  const companiesQuery = useQuery({ queryKey: queryKeys.companies, queryFn: getCompanies });

  useEffect(() => {
    if (!form.company_id && companiesQuery.data?.length === 1) {
      setForm((current) => ({ ...current, company_id: companiesQuery.data![0].id }));
    }
  }, [companiesQuery.data, form.company_id]);

  const companyMutation = useMutation({
    mutationFn: createCompany,
    onSuccess: async (company) => {
      setForm((current) => ({ ...current, company_id: company.id }));
      await queryClient.invalidateQueries({ queryKey: queryKeys.companies });
    }
  });
  const initMutation = useMutation({
    mutationFn: () =>
      createMinimalMarketChannel({
        ...form,
        known_destination_channel: form.known_destination_channel || null,
        account_country: form.account_country || null
      }),
    onSuccess: (result) => {
      setChannelId(result.channel.id);
      setDraft(null);
      setApprovedHash(null);
    }
  });
  const researchMutation = useMutation({
    mutationFn: () => {
      if (!channelId) throw new Error("Hãy lưu thông tin kênh trước.");
      return runTargetMarketDraft(channelId);
    },
    onSuccess: setDraft
  });
  const editMutation = useMutation({
    mutationFn: () => {
      if (!channelId || !draft) throw new Error("Chưa có research draft để lưu.");
      const expectedHash = draft.content_hash;
      const payload: Omit<TargetMarketProfileDraft, "content_hash"> & { content_hash?: string } = { ...draft };
      delete payload.content_hash;
      return updateTargetMarketDraft(channelId, payload, expectedHash);
    },
    onSuccess: setDraft
  });
  const approveMutation = useMutation({
    mutationFn: () => {
      if (!channelId || !draft) throw new Error("Chưa có bản nháp chính xác để duyệt.");
      return approveTargetMarketDraft(channelId, draft);
    },
    onSuccess: async (result) => {
      setApprovedHash(result.exact_approved_draft_hash);
      await queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    }
  });

  const failure = initMutation.error ?? researchMutation.error ?? editMutation.error ?? approveMutation.error ?? companyMutation.error;
  const busy = initMutation.isPending || researchMutation.isPending || editMutation.isPending || approveMutation.isPending;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Tạo kênh theo thị trường"
        subtitle="Thông tin tối thiểu → đề xuất nghiên cứu → người vận hành chỉnh sửa và duyệt đúng nội dung."
        breadcrumbs={[{ label: "Kênh", href: "/channels" }, { label: "Tạo kênh" }]}
      />

      <Panel className="border-primary/30">
        <p className="text-sm text-muted-foreground">
          VCOS tạo gói nội dung phù hợp thị trường; organic không có target country và không bảo đảm phân phối tới một quốc gia.
        </p>
      </Panel>

      <form
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault();
          initMutation.mutate();
        }}
      >
        <StepPanel number="1" title="Basics" icon={PencilLine}>
          {companiesQuery.data?.length ? (
            <Field label="Công ty *">
              <select className={controlClass} value={form.company_id} onChange={(event) => updateForm("company_id", event.target.value)} required>
                <option value="">Chọn công ty</option>
                {companiesQuery.data.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
              </select>
            </Field>
          ) : (
            <div className="rounded-md border border-border p-3 md:col-span-2">
              <p className="mb-3 text-sm font-medium">Tạo công ty trước</p>
              <div className="grid gap-3 md:grid-cols-3">
                <input aria-label="Tên công ty" className={controlClass} value={companyDraft.name} onChange={(event) => setCompanyDraft((current) => ({ ...current, name: event.target.value }))} />
                <input aria-label="Slug công ty" className={controlClass} value={companyDraft.slug} onChange={(event) => setCompanyDraft((current) => ({ ...current, slug: event.target.value }))} />
                <Button type="button" onClick={() => companyMutation.mutate(companyDraft)} disabled={companyMutation.isPending}>Tạo công ty</Button>
              </div>
            </div>
          )}
          <TextField label="Tên kênh *" value={form.channel_name} onChange={(value) => updateForm("channel_name", value)} required />
          <TextField label="Key kênh *" value={form.channel_key} onChange={(value) => updateForm("channel_key", value)} required />
          <TextField label="Mục đích kênh *" value={form.channel_purpose} onChange={(value) => updateForm("channel_purpose", value)} required multiline />
          <TextField label="Tóm tắt khán giả *" value={form.target_audience_summary} onChange={(value) => updateForm("target_audience_summary", value)} required multiline />
        </StepPanel>

        <StepPanel number="2" title="Target Market" icon={FileSearch}>
          <TextField label="Thị trường chính *" value={form.primary_market} onChange={(value) => updateForm("primary_market", value.toUpperCase())} required />
          <TextField label="Ngôn ngữ chính *" value={form.primary_language} onChange={(value) => updateForm("primary_language", value)} required />
          <TextField label="Locale chính *" value={form.primary_locale} onChange={(value) => updateForm("primary_locale", value)} required />
          <Field label="Kiểu thị trường *">
            <select className={controlClass} value={form.channel_market_type} onChange={(event) => updateForm("channel_market_type", event.target.value as InitForm["channel_market_type"])}>
              <option value="MARKET_NATIVE">Market-native</option>
              <option value="GLOBAL_ENGLISH">Global English</option>
            </select>
          </Field>
          <TextField label="Kênh đích đã biết" value={form.known_destination_channel} onChange={(value) => updateForm("known_destination_channel", value)} />
          <TextField label="Quốc gia tài khoản" value={form.account_country} onChange={(value) => updateForm("account_country", value.toUpperCase())} />
          <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100 md:col-span-2">
            Account country does not control organic audience targeting.
          </div>
          <div className="md:col-span-2">
            <Button type="submit" variant="primary" disabled={busy || !form.company_id}>
              Lưu thông tin tối thiểu
            </Button>
          </div>
        </StepPanel>
      </form>

      <StepPanel number="3" title="Research Draft" icon={FileSearch}>
        <div className="md:col-span-2">
          <Button type="button" onClick={() => researchMutation.mutate()} disabled={!channelId || busy}>
            Chạy Setup/Research Agent offline
          </Button>
          <p className="mt-2 text-xs text-muted-foreground">Đề xuất của agent không có quyền kích hoạt hay tự phê duyệt hồ sơ.</p>
        </div>
        {draft ? <ResearchSuggestions draft={draft} /> : <EmptyCopy text="Chưa có đề xuất nghiên cứu." />}
      </StepPanel>

      <StepPanel number="4" title="Human Review" icon={PencilLine}>
        {draft ? (
          <>
            <ListField label="Thị trường phụ" value={draft.acceptable_secondary_geos} onChange={(value) => setDraft({ ...draft, acceptable_secondary_geos: value })} />
            <TextField label="Locale giọng đọc" value={draft.narration_locale} onChange={(value) => setDraft({ ...draft, narration_locale: value })} />
            <TextField label="Múi giờ" value={draft.primary_timezone} onChange={(value) => setDraft({ ...draft, primary_timezone: value })} />
            <TextField label="Tiền tệ" value={draft.currency} onChange={(value) => setDraft({ ...draft, currency: value })} />
            <TextField label="Quy ước đơn vị" value={draft.units_policy} onChange={(value) => setDraft({ ...draft, units_policy: value })} />
            <TextField label="Quy tắc chính tả" value={draft.spelling_system} onChange={(value) => setDraft({ ...draft, spelling_system: value })} />
            <TextField label="Định dạng ngày" value={draft.date_format} onChange={(value) => setDraft({ ...draft, date_format: value })} />
            <TextField label="Bối cảnh thị trường" value={draft.audience_market_context} onChange={(value) => setDraft({ ...draft, audience_market_context: value, workplace_context: value })} />
            <TextField label="Chính sách nguồn theo pháp vực" value={draft.source_jurisdiction_policy} onChange={(value) => setDraft({ ...draft, source_jurisdiction_policy: value })} />
            <ListField label="Sai lệch bản địa hóa bị cấm" value={draft.prohibited_market_mismatches} onChange={(value) => setDraft({ ...draft, prohibited_market_mismatches: value })} />
            <Field label="Giả thuyết khung giờ publish">
              <textarea
                className={`${controlClass} min-h-28 font-mono text-xs`}
                value={JSON.stringify(draft.initial_publish_window_hypotheses, null, 2)}
                onChange={(event) => {
                  try {
                    const parsed = JSON.parse(event.target.value) as Array<Record<string, unknown>>;
                    if (Array.isArray(parsed)) setDraft({ ...draft, initial_publish_window_hypotheses: parsed });
                  } catch {
                    // Keep the last valid typed value while the operator is editing JSON.
                  }
                }}
              />
            </Field>
            <div className="md:col-span-2">
              <Button type="button" onClick={() => editMutation.mutate()} disabled={busy}>Lưu bản chỉnh sửa của người vận hành</Button>
            </div>
          </>
        ) : <EmptyCopy text="Chạy research draft để bắt đầu rà soát." />}
      </StepPanel>

      <StepPanel number="5" title="Approval" icon={ShieldCheck}>
        {draft ? (
          <div className="space-y-4 md:col-span-2">
            <div className="rounded-md border border-border bg-muted/20 p-3">
              <p className="text-sm font-medium">Nội dung chính xác chờ duyệt</p>
              <p className="mt-2 break-all font-mono text-xs text-muted-foreground">{draft.content_hash}</p>
              <p className="mt-2 text-sm text-muted-foreground">Phiên bản nháp {draft.draft_version}; mọi thay đổi sau duyệt phải tạo phiên bản và hash mới.</p>
            </div>
            <Button type="button" variant="primary" onClick={() => approveMutation.mutate()} disabled={busy || Boolean(approvedHash)}>
              <CheckCircle2 size={16} /> Duyệt đúng bản nháp này
            </Button>
            {approvedHash ? (
              <div className="rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">
                Đã duyệt đúng hash {approvedHash.slice(0, 12)}…. Hồ sơ sẵn sàng để compile ở phase profile v3; chưa có tự kích hoạt.
              </div>
            ) : null}
          </div>
        ) : <EmptyCopy text="Chưa có bản nháp để duyệt." />}
      </StepPanel>

      {failure ? <ErrorState message={failure.message} /> : null}
    </div>
  );

  function updateForm<Key extends keyof InitForm>(key: Key, value: InitForm[Key]) {
    setForm((current) => ({ ...current, [key]: value }));
  }
}

function ResearchSuggestions({ draft }: { draft: TargetMarketProfileDraft }) {
  return (
    <div className="space-y-3 md:col-span-2">
      <div className="grid gap-3 md:grid-cols-3">
        <Summary label="Trạng thái" value="Cần người vận hành rà soát" />
        <Summary label="Thông tin còn thiếu" value={draft.missing_information.length ? draft.missing_information.join(", ") : "Không có"} />
        <Summary label="Quyền của agent" value="Chỉ đề xuất" />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        {draft.suggestions.map((suggestion) => (
          <div key={suggestion.suggested_field} className="rounded-md border border-border p-3 text-sm">
            <div className="flex items-start justify-between gap-3">
              <p className="font-medium">{suggestionLabel(suggestion.suggested_field)}</p>
              <span className="rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">Tin cậy {Math.round(suggestion.confidence * 100)}%</span>
            </div>
            <p className="mt-2 text-muted-foreground">{displayValue(suggestion.suggested_value)}</p>
            <p className="mt-2 text-xs text-muted-foreground">Lý do: {suggestion.rationale}</p>
            <details className="mt-2 text-xs text-muted-foreground">
              <summary className="cursor-pointer">Bằng chứng ({suggestion.evidence_refs.length})</summary>
              <pre className="mt-2 overflow-auto whitespace-pre-wrap">{JSON.stringify(suggestion.evidence_refs, null, 2)}</pre>
            </details>
          </div>
        ))}
      </div>
    </div>
  );
}

function StepPanel({ number, title, icon: Icon, children }: { number: string; title: string; icon: typeof PencilLine; children: React.ReactNode }) {
  return (
    <Panel>
      <div className="mb-4 flex items-center gap-2">
        <Icon size={17} className="text-primary" />
        <h2 className="text-base font-semibold">{number}. {title}</h2>
      </div>
      <div className="grid gap-4 md:grid-cols-2">{children}</div>
    </Panel>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="space-y-2 text-sm"><span className="text-muted-foreground">{label}</span>{children}</label>;
}

function TextField({ label, value, onChange, required = false, multiline = false }: { label: string; value: string; onChange: (value: string) => void; required?: boolean; multiline?: boolean }) {
  return (
    <Field label={label}>
      {multiline ? (
        <textarea aria-label={label} className={`${controlClass} min-h-24`} value={value} onChange={(event) => onChange(event.target.value)} required={required} />
      ) : (
        <input aria-label={label} className={controlClass} value={value} onChange={(event) => onChange(event.target.value)} required={required} />
      )}
    </Field>
  );
}

function ListField({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => setText(value.join("\n")), [value]);
  return (
    <Field label={label}>
      <textarea
        aria-label={label}
        className={`${controlClass} min-h-24`}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onBlur={() => onChange(text.split(/\n|,/).map((item) => item.trim()).filter(Boolean))}
      />
    </Field>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-border bg-muted/20 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-sm font-medium">{value}</p></div>;
}

function EmptyCopy({ text }: { text: string }) {
  return <p className="text-sm text-muted-foreground md:col-span-2">{text}</p>;
}

function suggestionLabel(field: string) {
  return ({
    acceptable_secondary_geos: "Thị trường phụ",
    narration_locale: "Locale giọng đọc",
    primary_timezone: "Múi giờ",
    currency: "Tiền tệ",
    units_policy: "Quy ước đơn vị",
    spelling_system: "Quy tắc chính tả",
    date_format: "Định dạng ngày",
    audience_market_context: "Bối cảnh khán giả",
    workplace_context: "Bối cảnh nơi làm việc",
    source_jurisdiction_policy: "Chính sách pháp vực nguồn",
    prohibited_market_mismatches: "Sai lệch bị cấm",
    initial_publish_window_hypotheses: "Giả thuyết khung giờ publish",
    market_terminology_notes: "Ghi chú thuật ngữ thị trường"
  } as Record<string, string>)[field] ?? field;
}

function displayValue(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

const controlClass = "block w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground outline-none focus:border-primary";
