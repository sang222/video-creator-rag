import type {
  ChannelSummary,
  ChannelWorkspace,
  CommandCenter,
  DashboardQueues,
  LearningDecisionPayload,
  ProviderOps,
  UploadedVideoDashboard,
  UploadedVideoListItem
} from "./types";

export const apiBaseUrl = process.env.NEXT_PUBLIC_VCOS_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
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
  uploadedVideos: ["uploaded-videos"],
  uploadedVideo: (uploadedVideoId: string) => ["uploaded-video", uploadedVideoId],
  providerOps: ["provider-ops"]
} as const;

export function getCommandCenter() {
  return request<CommandCenter>("/dashboard/command-center");
}

export function getQueues(queueType?: string) {
  return request<DashboardQueues>(queueType ? `/dashboard/queues/${queueType}` : "/dashboard/queues");
}

export function getChannels() {
  return request<ChannelSummary[]>("/channels");
}

export function getChannelWorkspace(channelId: string) {
  return request<ChannelWorkspace>(`/channels/${channelId}/workspace`);
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
  primary_language: string;
  target_market?: string;
  long_form_target_minutes: number;
  short_form_length_seconds: number;
  tts_character_budget: number;
  ai_hero_budget_usd: number;
  derivative_shorts_per_long_form: number;
  drive_offload_enabled: boolean;
};

export async function initChannel(input: ChannelInitInput) {
  const channel = await request<{ id: string }>(`/companies/${input.company_id}/channels`, {
    method: "POST",
    body: JSON.stringify({
      key: input.key,
      name: input.name,
      status: "draft",
      primary_language: input.primary_language,
      target_market: input.target_market || null,
      metadata: {
        m11_channel_init: {
          long_form_target_minutes: input.long_form_target_minutes,
          short_form_length_seconds: input.short_form_length_seconds,
          tts_character_budget: input.tts_character_budget,
          ai_hero_budget_usd: input.ai_hero_budget_usd,
          derivative_shorts_per_long_form: input.derivative_shorts_per_long_form,
          drive_offload_enabled: input.drive_offload_enabled,
          no_ai_config_suggestion: true
        }
      }
    })
  });
  const profile = await request<{ id: string }>(`/channels/${channel.id}/profile-versions`, {
    method: "POST",
    body: JSON.stringify({ template_key: input.template_key })
  });
  const compiled = await request<{ snapshot_id: string }>(`/profile-versions/${profile.id}/compile`, {
    method: "POST",
    body: JSON.stringify({})
  });
  const snapshot = await request<Record<string, unknown>>(`/policy-snapshots/${compiled.snapshot_id}/activate`, {
    method: "POST"
  });
  return { channel, profile, compiled, snapshot };
}
