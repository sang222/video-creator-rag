import type {
  ChannelSummary,
  ChannelWorkspace,
  Company,
  CommandCenter,
  DashboardQueues,
  IntegrationReadiness,
  LearningDecisionPayload,
  ProviderOps,
  RealSmokeRun,
  UploadedVideoDashboard,
  UploadedVideoListItem,
  AuthSession,
  BackfillUploadedVideoInput,
  BackfillUploadedVideoResult,
  HumanUploadTask,
  HumanUploadTaskList,
  PublishLedger,
  UploadedVideoLedgerList
} from "./types";

export const apiBaseUrl = process.env.NEXT_PUBLIC_VCOS_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export const queryKeys = {
  commandCenter: ["command-center"],
  queues: (queueType?: string) => ["queues", queueType ?? "all"],
  channels: ["channels"],
  channelWorkspace: (channelId: string) => ["channel-workspace", channelId],
  channelPublishLedger: (channelId: string) => ["channel-publish-ledger", channelId],
  channelUploadTasks: (channelId: string) => ["channel-upload-tasks", channelId],
  channelUploadedVideos: (channelId: string) => ["channel-uploaded-videos", channelId],
  uploadedVideos: ["uploaded-videos"],
  uploadedVideo: (uploadedVideoId: string) => ["uploaded-video", uploadedVideoId],
  channelLifecycle: (channelId: string) => ["channel-lifecycle", channelId],
  companies: ["companies"],
  providerOps: ["provider-ops"],
  integrationsReadiness: ["integrations-readiness"]
} as const;

export function getCurrentUser() {
  return request<AuthSession>("/auth/me");
}

export function login(email: string, password: string) {
  return request<AuthSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export function logout() {
  return request<{ status: string; message: string }>("/auth/logout", { method: "POST" });
}

export function getCommandCenter() {
  return request<CommandCenter>("/dashboard/command-center");
}

export function getQueues(queueType?: string) {
  return request<DashboardQueues>(queueType ? `/dashboard/queues/${queueType}` : "/dashboard/queues");
}

export function getChannels() {
  return request<ChannelSummary[]>("/channels");
}

export function getCompanies() {
  return request<Company[]>("/companies");
}

export function createCompany(input: { name: string; slug: string }) {
  return request<Company>("/companies", {
    method: "POST",
    body: JSON.stringify({ name: input.name, slug: input.slug })
  });
}

export function getChannelWorkspace(channelId: string) {
  return request<ChannelWorkspace>(`/channels/${channelId}/workspace`);
}

export function getChannelPublishLedger(channelId: string) {
  return request<PublishLedger>(`/channels/${channelId}/publish-ledger`);
}

export function getChannelUploadTasks(channelId: string) {
  return request<HumanUploadTaskList>(`/channels/${channelId}/upload-tasks`);
}

export function getChannelUploadedVideos(channelId: string) {
  return request<UploadedVideoLedgerList>(`/channels/${channelId}/uploaded-videos`);
}

export function startUploadTask(taskId: string) {
  return request<HumanUploadTask>(`/upload-tasks/${taskId}/start`, { method: "POST", body: JSON.stringify({}) });
}

export function backfillUploadedVideo(taskId: string, input: BackfillUploadedVideoInput) {
  return request<BackfillUploadedVideoResult>(`/upload-tasks/${taskId}/backfill-uploaded-video`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function verifyUploadedVideo(uploadedVideoId: string) {
  return request<Record<string, unknown>>(`/uploaded-videos/${uploadedVideoId}/verify`, { method: "POST", body: JSON.stringify({}) });
}

export function getUploadedVideos() {
  return request<UploadedVideoListItem[]>("/uploaded-videos");
}

export function getUploadedVideoDashboard(uploadedVideoId: string) {
  return request<UploadedVideoDashboard>(`/uploaded-videos/${uploadedVideoId}/dashboard`);
}

export function getProviderOps() {
  return request<ProviderOps>("/providers/status");
}

export function getIntegrationsReadiness() {
  return request<IntegrationReadiness>("/integrations/readiness");
}

export function runIntegrationsReadiness() {
  return request<Record<string, unknown>>("/integrations/readiness/run", { method: "POST", body: JSON.stringify({}) });
}

export function runProviderSmoke(providerKey: string) {
  return request<RealSmokeRun>(`/integrations/providers/${providerKey}/smoke`, { method: "POST", body: JSON.stringify({}) });
}

export function decideLearningCandidate(candidateId: string, payload: LearningDecisionPayload) {
  const actionPath = {
    APPROVE: "approve",
    REJECT: "reject",
    REQUEST_MORE_EVIDENCE: "request-more-evidence",
    SUPPRESS: "suppress",
    EXPIRE: "expire"
  }[payload.action];
  return request<Record<string, unknown>>(`/learning-candidates/${candidateId}/${actionPath}`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export type ChannelInitInput = {
  company_id: string;
  key: string;
  name: string;
  template_key: string;
  channel_type: string;
  niche: string;
  positioning: string;
  brand_promise: string;
  secondary_platforms: string;
  primary_persona: string;
  audience_level: string;
  pain_points: string;
  desired_outcome: string;
  audience_notes: string;
  primary_market: string;
  secondary_markets: string;
  audience_locale: string;
  content_language: string;
  operator_language: string;
  timezone: string;
  currency: string;
  measurement_units: string;
  date_format: string;
  cultural_tone: string;
  cultural_formality: string;
  cultural_humor: string;
  cta_style: string;
  market_examples_preference: string;
  finance_claim_sensitivity: string;
  health_claim_sensitivity: string;
  disclosure_standard: string;
  content_pillars: string;
  allowed_angles: string;
  forbidden_angles: string;
  allowed_topics: string;
  forbidden_topics: string;
  long_form_enabled: boolean;
  long_form_min_minutes: number;
  long_form_max_minutes: number;
  shorts_enabled: boolean;
  shorts_min_seconds: number;
  shorts_max_seconds: number;
  shorts_hard_max_seconds: number;
  captions_required: boolean;
  chapters_required_for_long_form: boolean;
  derivative_shorts_per_long_form: number;
  narration_tone: string;
  pacing: string;
  allowed_style: string;
  forbidden_style: string;
  cost_sensitivity: string;
  avoid_unnecessary_ai_hero: boolean;
  prefer_reuse_safe_assets: boolean;
  exact_cost_claim_requires_provider_snapshot: boolean;
  min_evidence_required: string;
  reused_content_sensitivity: string;
  drive_offload_enabled: boolean;
};

export async function initChannel(input: ChannelInitInput) {
  const contentPillars = toLines(input.content_pillars);
  const secondaryMarkets = toLines(input.secondary_markets);
  const secondaryPlatforms = toLines(input.secondary_platforms);
  const formatPolicy = {
    long_form: {
      enabled: input.long_form_enabled,
      target_duration_minutes: { min: input.long_form_min_minutes, max: input.long_form_max_minutes },
      structure: ["hook", "problem", "mechanism", "result", "takeaway"],
      chapters_required: input.chapters_required_for_long_form
    },
    shorts: {
      enabled: input.shorts_enabled,
      target_duration_seconds: { min: input.shorts_min_seconds, max: input.shorts_max_seconds },
      hard_max_seconds: input.shorts_hard_max_seconds,
      captions_required: input.captions_required,
      shorts_per_long_form: input.derivative_shorts_per_long_form
    }
  };
  const channelContract = {
    channel_identity: {
      company_id: input.company_id,
      channel_key: input.key,
      channel_name: input.name,
      template_key: input.template_key,
      channel_type: input.channel_type,
      niche: input.niche,
      positioning: input.positioning,
      brand_promise: input.brand_promise,
      primary_platform: "YouTube",
      secondary_platforms: secondaryPlatforms
    },
    target_audience: {
      primary_persona: input.primary_persona,
      audience_level: input.audience_level,
      pain_points: toLines(input.pain_points),
      desired_outcome: input.desired_outcome,
      audience_notes: input.audience_notes
    },
    market_locale: {
      primary_market: input.primary_market || null,
      secondary_markets: secondaryMarkets,
      audience_locale: input.audience_locale || null,
      content_language: input.content_language || null,
      operator_language: input.operator_language || "vi",
      timezone: input.timezone || null,
      currency: input.currency || null,
      measurement_units: input.measurement_units,
      date_format: input.date_format,
      cultural_style: {
        tone: input.cultural_tone,
        formality: input.cultural_formality,
        humor: input.cultural_humor,
        cta_style: input.cta_style
      },
      market_examples_preference: input.market_examples_preference,
      regulatory_sensitivity: {
        finance_claim_sensitivity: input.finance_claim_sensitivity,
        health_claim_sensitivity: input.health_claim_sensitivity,
        disclosure_standard: input.disclosure_standard
      }
    },
    editorial_strategy: {
      content_pillars: contentPillars,
      allowed_angles: toLines(input.allowed_angles),
      forbidden_angles: toLines(input.forbidden_angles),
      claim_style: ["measured", "evidence_backed", "no_exaggerated_roi"],
      allowed_topics: toLines(input.allowed_topics),
      forbidden_topics: toLines(input.forbidden_topics)
    },
    format_policy: formatPolicy,
    voice_style: {
      narration_tone: input.narration_tone,
      pacing: input.pacing,
      allowed_style: toLines(input.allowed_style),
      forbidden_style: toLines(input.forbidden_style)
    },
    platform_strategy: {
      primary_platform: "YouTube",
      youtube_is_learning_authority: true,
      secondary_platforms: secondaryPlatforms,
      disabled_authorities: ["tiktok_analytics_learning", "facebook_analytics_learning"],
      publish_mode: "human_handoff_only",
      auto_publish_allowed: false,
      studio_scraping_allowed: false
    },
    media_policy: {
      voice_provider: "ElevenLabs",
      ai_hero_provider: "Google Vertex Veo",
      ai_hero_model_id: "veo-3.1-fast-generate-001",
      ai_hero_allowed_durations_seconds: [4, 6, 8],
      ai_hero_default_duration_seconds: 8,
      ai_hero_audio: false,
      ai_hero_allowed_use: ["hero_shot", "hard_to_find_visual"],
      ai_hero_forbidden_use: ["data_diagram", "workflow_chart", "factual_evidence_visualization"],
      renderer: "Creatomate Growth 10K",
      storage_archive: "Google Drive",
      drive_offload_enabled: input.drive_offload_enabled
    },
    rights_policy: {
      source_manifest_required: true,
      rights_evidence_required: true,
      ai_disclosure_required_when_ai_media_used: true,
      synthetic_media_warning_when_applicable: true,
      music_policy: "approved_licensed_audio_library_safe_only",
      reused_content_sensitivity: input.reused_content_sensitivity
    },
    budget_policy: {
      cost_sensitivity: input.cost_sensitivity,
      avoid_unnecessary_ai_hero: input.avoid_unnecessary_ai_hero,
      prefer_reuse_safe_assets: input.prefer_reuse_safe_assets,
      exact_cost_claim_requires_provider_snapshot: input.exact_cost_claim_requires_provider_snapshot
    },
    learning_policy: {
      authority: "youtube_analytics_only",
      min_evidence_required: input.min_evidence_required,
      auto_promote_learning: false,
      config_mutation_by_agent_allowed: false,
      weak_evidence_action: "summarize_limitations_only"
    },
    forbidden_behavior: [
      "fake_traffic",
      "bot_engagement",
      "spam_reupload",
      "algorithm_manipulation",
      "platform_evasion",
      "ip_vps_tricks",
      "youtube_studio_scraping",
      "dashboard_scraping",
      "invented_metrics",
      "invented_sources",
      "invented_rights",
      "unsupported_local_claims"
    ]
  };
  const channel = await request<{ id: string }>(`/companies/${input.company_id}/channels`, {
    method: "POST",
    body: JSON.stringify({
      key: input.key,
      name: input.name,
      status: "draft",
      primary_language: input.content_language,
      primary_region: input.primary_market || null,
      primary_timezone: input.timezone,
      target_market: input.primary_market || null,
      default_timezone: input.timezone,
      target_regions: secondaryMarkets,
      metadata: {
        operator_language: input.operator_language || "vi",
        currency: input.currency,
        m12_2p_channel_contract: channelContract,
        no_ai_config_suggestion: true
      }
    })
  });
  const profile = await request<{ id: string }>(`/channels/${channel.id}/profile-versions`, {
    method: "POST",
    body: JSON.stringify({
      profile_input: {
        template_key: input.template_key,
        template_version: "1.0.0",
        display_name: input.name,
        target_market: input.primary_market,
        audience_segment: "professional_dense",
        monetization_model: { primary: "mixed", channels: ["adsense", "affiliate"] },
        format_strategy: formatPolicy,
        risk_tolerance: "low_to_medium",
        media_style: { visual_bias: ["screenshots", "workflow_diagrams", "safe_reuse_assets"], external_assets: "approved/licensed/audio-library-safe only" },
        voice_style: { narration_tone: input.narration_tone, pacing: input.pacing },
        evidence_requirement: { claims: input.min_evidence_required, cite_when: "claim is non-obvious" },
        platform_strategy: channelContract.platform_strategy,
        human_review_strictness: "strict",
        content_pillars: contentPillars.length ? contentPillars : [input.niche],
        series_plan: [{ key: "operator_series", name: input.niche, format: "long_form_and_shorts" }],
        initial_content_runway: [{ title: input.niche, format: "long_form" }],
        policies: {
          review: "human_review_for_non_obvious_claims",
          safety: "avoid unsupported claims",
          channel_contract: channelContract
        }
      }
    })
  });
  const compiled = await request<Record<string, unknown>>(`/channels/${channel.id}/compile-policy-snapshot`, {
    method: "POST",
    body: JSON.stringify({})
  });
  return { channel, profile, compiled, snapshot: compiled };
}

export function activateChannel(channelId: string, snapshotId?: string) {
  return request<Record<string, unknown>>(`/channels/${channelId}/activate`, {
    method: "POST",
    body: JSON.stringify({ snapshot_id: snapshotId ?? null })
  });
}

export function postLifecycleDecision(channelId: string, action: string, reason?: string) {
  return request<Record<string, unknown>>(`/channels/${channelId}/lifecycle-decision`, {
    method: "POST",
    body: JSON.stringify({ action, reason: reason ?? `Operator activated channel via CTA`, actor_role: "OWNER_ADMIN" })
  });
}

function toLines(value: string | undefined | null): string[] {
  return String(value ?? "")
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}
