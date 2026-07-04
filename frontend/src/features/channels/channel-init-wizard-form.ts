import { z } from "zod";

const requiredText = z.string().trim().min(1, "Bắt buộc nhập.");

export const minimalSchema = z.object({
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

export type MinimalFormValues = z.infer<typeof minimalSchema>;

export const advancedSchema = z.object({
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

export type FormValues = z.infer<typeof advancedSchema>;

export const marketOptions = [["US", "US"], ["UK", "UK"], ["EU", "EU"], ["JP", "JP"], ["KR", "KR"], ["VN", "VN"], ["AU", "AU"], ["CA", "CA"], ["OTHER", "OTHER"]];
export const localeOptions = [["en-US", "English US"], ["en-GB", "English UK"], ["ja-JP", "Japanese"], ["ko-KR", "Korean"], ["vi-VN", "Tiếng Việt"], ["other", "Other"]];
export const dateFormatOptions = [["MM/DD/YYYY", "MM/DD/YYYY"], ["DD/MM/YYYY", "DD/MM/YYYY"], ["YYYY/MM/DD", "YYYY/MM/DD"], ["YYYY.MM.DD", "YYYY.MM.DD"]];

export function splitLines(value: string | undefined | null): string[] {
  return String(value ?? "")
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function contractPreview(values: Partial<FormValues>) {
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
