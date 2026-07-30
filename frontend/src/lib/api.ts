import type {
  ChannelSummary,
  ChannelWorkspace,
  ChannelContractDraft,
  ChannelInitCompileResult,
  ChannelInitDraft,
  Company,
  CommandCenter,
  DashboardQueues,
  IntegrationReadiness,
  LearningDecisionPayload,
  ProviderOps,
  ProductionCockpit,
  RealSmokeRun,
  UploadedVideoDashboard,
  UploadedVideoListItem,
  AuthSession,
  BackfillUploadedVideoInput,
  BackfillUploadedVideoResult,
  HumanUploadTask,
  HumanUploadTaskList,
  MemoryInfluenceOps,
  ManualPublish,
  ManualPublishConfirmationInput,
  OpsQueue,
  OperatorPlanningCatalog,
  OperatorPlanningLaunch,
  OperatorPlanningPrepare,
  PackagingApplyApprovedChangesResult,
  PackagingPatchApplyRun,
  PackagingPatchApprovalDecision,
  PackagingGateRerunRecord,
  PackagingReviewQueue,
  PackageOpsSummary,
  PublishLedger,
  ProviderCostOps,
  QualityDeltaOps,
  RetrievalManifestOps,
  RuntimeOpsCommandCenter,
  ChannelRuntimeTrace,
  ChannelProfileManagement,
  DestinationBinding,
  TargetMarketPreview,
  TargetMarketProfile,
  TargetMarketProfileDraft,
  VideoPackageReview,
  UploadedVideoOpsSummary,
  UploadedVideoLedgerList
} from "./types";

export const apiBaseUrl = process.env.NEXT_PUBLIC_VCOS_API_BASE_URL ?? "http://localhost:8000";

export function safeReviewMediaUrl(value?: string | null) {
  if (!value) return null;
  try {
    if (value.startsWith("/")) {
      const base = new URL(apiBaseUrl);
      const resolved = new URL(value, base);
      const mediaPath =
        /^\/final-review-candidates\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(?:media|thumbnail)$/;
      const allowedQuery =
        resolved.search === "" || resolved.search === "?download=1";
      return resolved.origin === base.origin &&
        mediaPath.test(resolved.pathname) &&
        allowedQuery
        ? resolved.toString()
        : null;
    }
    const resolved = new URL(value);
    return resolved.protocol === "https:" ? resolved.toString() : null;
  } catch {
    return null;
  }
}

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
  channelProfileManagement: (channelId: string) => ["channel-profile-management", channelId],
  targetMarketPreview: (channelId: string) => ["target-market-preview", channelId],
  destinationBinding: (channelId: string) => ["destination-binding", channelId],
  channelPublishLedger: (channelId: string) => ["channel-publish-ledger", channelId],
  channelUploadTasks: (channelId: string) => ["channel-upload-tasks", channelId],
  channelUploadedVideos: (channelId: string) => ["channel-uploaded-videos", channelId],
  videoPackageReview: (packageId: string) => ["video-package-review", packageId],
  packagingReviewQueue: (packageId: string) => ["packaging-review-queue", packageId],
  uploadedVideos: ["uploaded-videos"],
  uploadedVideo: (uploadedVideoId: string) => ["uploaded-video", uploadedVideoId],
  channelLifecycle: (channelId: string) => ["channel-lifecycle", channelId],
  companies: ["companies"],
  providerOps: ["provider-ops"],
  integrationsReadiness: ["integrations-readiness"],
  runtimeOpsCommandCenter: ["runtime-ops-command-center"],
  opsNextActions: ["runtime-ops-next-actions"],
  channelRuntimeTrace: (channelId: string) => ["channel-runtime-trace", channelId],
  projectRuntimeTrace: (projectId: string) => ["project-runtime-trace", projectId],
  packageOpsSummary: (packageId: string) => ["package-ops-summary", packageId],
  uploadedVideoOpsSummary: (uploadedVideoId: string) => ["uploaded-video-ops-summary", uploadedVideoId],
  diagnosticsQueue: ["diagnostics-queue"],
  recoveryQueue: ["recovery-queue"],
  learningOpsQueue: ["learning-ops-queue"],
  memoryOpsQueue: ["memory-ops-queue"],
  retrievalManifestOps: (manifestId: string) => ["retrieval-manifest-ops", manifestId],
  memoryInfluenceOps: (manifestId: string) => ["memory-influence-ops", manifestId],
  qualityDeltaOps: (qualityDeltaId: string) => ["quality-delta-ops", qualityDeltaId],
  providerCostOps: (packageId: string) => ["provider-cost-ops", packageId],
  productionCockpit: (projectId?: string) => [
    "production-cockpit",
    projectId ?? "next"
  ],
  operatorPlanning: ["operator-planning"]
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

export function getProductionCockpit(projectId?: string) {
  return request<ProductionCockpit>(
    projectId
      ? `/video-projects/${projectId}/operator-cockpit`
      : "/operator-cockpit"
  );
}

export function getOperatorPlanningCatalog() {
  return request<OperatorPlanningCatalog>("/operator-planning/catalog");
}

export function prepareOperatorPlanningSource(input: {
  sourceType: "DAILY_SLOT" | "DAILY_IDEA" | "LONG_FORM_PLAN";
  sourceId: string;
  maxBudgetUsd?: number;
}) {
  return request<OperatorPlanningPrepare>("/operator-planning/prepare", {
    method: "POST",
    body: JSON.stringify({
      source_type: input.sourceType,
      source_id: input.sourceId,
      max_budget_usd: input.maxBudgetUsd ?? 0
    })
  });
}

export function prepareAndLaunchOperatorPlanningSource(input: {
  sourceType: "DAILY_SLOT" | "DAILY_IDEA" | "LONG_FORM_PLAN";
  sourceId: string;
  maxBudgetUsd?: number;
}) {
  return request<OperatorPlanningLaunch>("/operator-planning/launch", {
    method: "POST",
    body: JSON.stringify({
      source_type: input.sourceType,
      source_id: input.sourceId,
      max_budget_usd: input.maxBudgetUsd ?? 0,
      idempotency_key: operatorPlanningIdempotencyKey(
        input.sourceType,
        input.sourceId
      )
    })
  });
}

function operatorPlanningIdempotencyKey(
  sourceType: "DAILY_SLOT" | "DAILY_IDEA" | "LONG_FORM_PLAN",
  sourceId: string
) {
  return `operator-planning:${sourceType}:${sourceId}`;
}

export function launchDailyShortPlanning(
  dailyIdeaDecisionId: string,
  maxBudgetUsd = 0
) {
  return request<OperatorPlanningLaunch>(
    "/operator-planning/daily-short/launch",
    {
      method: "POST",
      body: JSON.stringify({
        daily_idea_decision_id: dailyIdeaDecisionId,
        max_budget_usd: maxBudgetUsd,
        idempotency_key: operatorPlanningIdempotencyKey(
          "DAILY_IDEA",
          dailyIdeaDecisionId
        )
      })
    }
  );
}

export function launchLongFormPlanning(input: {
  editorialCalendarSlotId: string;
  maxBudgetUsd?: number;
}) {
  return request<OperatorPlanningLaunch>(
    "/operator-planning/long-form/launch",
    {
      method: "POST",
      body: JSON.stringify({
        editorial_calendar_slot_id: input.editorialCalendarSlotId,
        max_budget_usd: input.maxBudgetUsd ?? 0,
        idempotency_key: operatorPlanningIdempotencyKey(
          "LONG_FORM_PLAN",
          input.editorialCalendarSlotId
        )
      })
    }
  );
}

export function startProjectProduction(projectId: string, companyId: string) {
  return request<Record<string, unknown>>(
    `/video-projects/${projectId}/production-workflow/start?company_id=${encodeURIComponent(companyId)}`,
    {
      method: "POST",
      body: JSON.stringify({})
    }
  );
}

export function resumeProductionWorkflow(workflowRunId: string, companyId: string) {
  return request<Record<string, unknown>>(
    `/production-workflows/${workflowRunId}/resume?company_id=${encodeURIComponent(companyId)}`,
    {
      method: "POST",
      body: JSON.stringify({ reason_code: "OPERATOR_RESUME" })
    }
  );
}

export function cancelProductionWorkflow(workflowRunId: string, companyId: string) {
  return request<Record<string, unknown>>(
    `/production-workflows/${workflowRunId}/cancel?company_id=${encodeURIComponent(companyId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        reason: "Người vận hành yêu cầu dừng an toàn từ buồng lái."
      })
    }
  );
}

export function decideFinalVideo(
  candidateId: string,
  decision: "UPLOAD" | "DO_NOT_UPLOAD",
  warningsAcknowledged: string[] = []
) {
  return request<Record<string, unknown>>(
    `/final-review-candidates/${candidateId}/decisions`,
    {
      method: "POST",
      body: JSON.stringify({
        command_id: newCommandId(),
        decision,
        warnings_acknowledged: warningsAcknowledged
      })
    }
  );
}

export function startManualUpload(taskId: string, publish: ManualPublish) {
  const archiveObjectRef = technicalString(
    publish.technical_appendix,
    "archive_object_ref"
  );
  if (!archiveObjectRef) {
    return Promise.reject(
      new Error(
        "File lưu trữ chưa có reference an toàn. Không thể bắt đầu upload thủ công."
      )
    );
  }
  return request<Record<string, unknown>>(
    `/human-upload-tasks/${taskId}/start`,
    {
      method: "POST",
      body: JSON.stringify({
        selected_file_name: publish.exact_file_name,
        selected_file_ref: archiveObjectRef,
        selected_file_checksum: publish.reviewed_checksum_sha256,
        archive_object_ref: archiveObjectRef
      })
    }
  );
}

export function submitManualPublishConfirmation(
  taskId: string,
  publish: ManualPublish,
  input: ManualPublishConfirmationInput
) {
  return request<Record<string, unknown>>(
    `/human-upload-tasks/${taskId}/manual-publish-confirmations`,
    {
      method: "POST",
      body: JSON.stringify(manualConfirmationPayload(publish, input))
    }
  );
}

export function correctManualPublishConfirmation(
  publish: ManualPublish,
  input: ManualPublishConfirmationInput
) {
  const confirmationId = technicalString(
    publish.technical_appendix,
    "confirmation_id"
  );
  if (!confirmationId) {
    return Promise.reject(
      new Error("Chưa có xác nhận gốc để lưu correction.")
    );
  }
  return request<Record<string, unknown>>(
    `/manual-publish-confirmations/${confirmationId}/corrections`,
    {
      method: "POST",
      body: JSON.stringify({
        ...manualConfirmationPayload(publish, input),
        command_id: undefined,
        correction_command_id: newCommandId()
      })
    }
  );
}

export async function verifyManualPublishConfirmation(
  publish: ManualPublish
) {
  const confirmationId = technicalString(
    publish.technical_appendix,
    "confirmation_id"
  );
  const destinationIdentity = technicalString(
    publish.technical_appendix,
    "destination_account_identity"
  );
  if (
    !confirmationId ||
    !publish.destination_channel_id ||
    !destinationIdentity ||
    !publish.platform_video_id ||
    !publish.platform_video_url ||
    !publish.actual_title ||
    !publish.actual_visibility ||
    !publish.actual_published_at ||
    !publish.actual_duration_seconds
  ) {
    throw new Error(
      "Xác nhận chưa đủ dữ liệu quan sát để tạo UploadedVideo đã xác minh."
    );
  }
  const observation = {
    observed_platform: publish.target_platform,
    observed_platform_channel_id: publish.destination_channel_id,
    observed_destination_account_identity: destinationIdentity,
    observed_platform_video_id: publish.platform_video_id,
    observed_video_url: publish.platform_video_url,
    observed_title: publish.actual_title,
    observed_description: publish.actual_description ?? null,
    observed_privacy_status: publish.actual_visibility,
    observed_published_at: publish.actual_published_at,
    observed_duration_seconds: publish.actual_duration_seconds
  };
  return request<Record<string, unknown>>(
    `/manual-publish-confirmations/${confirmationId}/verification`,
    {
      method: "POST",
      body: JSON.stringify({
        verification_command_id: newCommandId(),
        verification_evidence_ref:
          `operator-observation://manual-publish/${confirmationId}`,
        ...observation
      })
    }
  );
}

export function getQueues(queueType?: string) {
  return request<DashboardQueues>(queueType ? `/dashboard/queues/${queueType}` : "/dashboard/queues");
}

function manualConfirmationPayload(
  publish: ManualPublish,
  input: ManualPublishConfirmationInput
) {
  const destinationBindingId = technicalString(
    publish.technical_appendix,
    "destination_binding_id"
  );
  const destinationBindingFingerprint = technicalString(
    publish.technical_appendix,
    "destination_binding_fingerprint"
  );
  const destinationIdentity =
    publish.destination_handle ??
    technicalString(
      publish.technical_appendix,
      "destination_account_identity"
    );
  if (
    !destinationBindingId ||
    !destinationBindingFingerprint ||
    !publish.destination_channel_id ||
    !destinationIdentity
  ) {
    throw new Error(
      "Đích publish chưa đủ lineage để ghi xác nhận. Hãy kiểm tra cấu hình kênh."
    );
  }
  return {
    command_id: newCommandId(),
    platform: publish.target_platform,
    platform_channel_id: publish.destination_channel_id,
    destination_binding_id: destinationBindingId,
    destination_binding_fingerprint: destinationBindingFingerprint,
    destination_account_identity: destinationIdentity,
    platform_video_id: input.platform_video_id,
    video_url: input.platform_video_url,
    title: input.actual_title,
    description: input.actual_description,
    privacy_status: input.actual_visibility,
    published_at: input.published_at,
    duration_seconds: input.duration_seconds,
    thumbnail_confirmed: input.thumbnail_matches,
    caption_confirmed: input.captions_match,
    playlist_id: input.playlist_id || null,
    playlist_order: input.playlist_order,
    disclosures: {
      ai_disclosure_confirmed: input.ai_disclosure_confirmed,
      rights_confirmed: input.rights_confirmed
    },
    accept_non_material_variance: input.accept_non_material_variance,
    operator_notes: input.operator_notes || null
  };
}

function technicalString(
  appendix: Record<string, unknown>,
  key: string
): string | null {
  const value = appendix[key];
  return typeof value === "string" && value.length ? value : null;
}

function newCommandId() {
  return crypto.randomUUID();
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

export type MinimalChannelInitInput = {
  company_id: string;
  channel_name: string;
  public_presence_mode: "EXISTING_PUBLIC_CHANNEL" | "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT";
  youtube_url_or_handle?: string | null;
  website_url?: string | null;
  social_profile_links?: string[];
  operator_note_purpose: string;
  intended_content_language?: string | null;
  intended_primary_market?: string | null;
  owner_operator_language: string;
  initial_topic_pillar_hints?: string[];
  source_usage_attestation: boolean;
};

export function createChannelInitDraft(input: MinimalChannelInitInput) {
  return request<ChannelInitDraft>("/channel-init-drafts", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export type MinimalMarketChannelInitInput = {
  company_id: string;
  channel_name: string;
  channel_key: string;
  channel_purpose: string;
  primary_market: string;
  primary_language: string;
  primary_locale: string;
  target_audience_summary: string;
  channel_market_type: "MARKET_NATIVE" | "GLOBAL_ENGLISH";
  known_destination_channel?: string | null;
  account_country?: string | null;
};

export function createMinimalMarketChannel(input: MinimalMarketChannelInitInput) {
  return request<{
    channel: ChannelSummary;
    target_market_state: string;
    profile_activation_allowed: false;
    organic_target_country_supported: false;
  }>("/channels/init", { method: "POST", body: JSON.stringify(input) });
}

export function runTargetMarketDraft(channelId: string) {
  return request<TargetMarketProfileDraft>(`/channels/${channelId}/target-market-draft/run`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function getTargetMarketDraft(channelId: string) {
  return request<TargetMarketProfileDraft>(`/channels/${channelId}/target-market-draft`);
}

export function updateTargetMarketDraft(
  channelId: string,
  draft: Omit<TargetMarketProfileDraft, "content_hash"> & { content_hash?: string },
  expectedDraftHash: string
) {
  return request<TargetMarketProfileDraft>(`/channels/${channelId}/target-market-draft`, {
    method: "PATCH",
    body: JSON.stringify({ expected_draft_hash: expectedDraftHash, draft })
  });
}

export function approveTargetMarketDraft(
  channelId: string,
  draft: TargetMarketProfileDraft,
  reviewer = "operator"
) {
  return request<{
    decision: "APPROVE";
    profile: TargetMarketProfile;
    profile_activation_allowed: false;
    exact_approved_draft_hash: string;
  }>(`/channels/${channelId}/target-market-draft/approve`, {
    method: "POST",
    body: JSON.stringify({
      expected_draft_id: draft.draft_id,
      expected_draft_version: draft.draft_version,
      expected_draft_hash: draft.content_hash,
      reviewer,
      approval_ref: `operator-approval://target-market/${channelId}/draft-${draft.draft_version}`,
      decision: "APPROVE"
    })
  });
}

export function getTargetMarketPreview(channelId: string) {
  return request<TargetMarketPreview>(`/channels/${channelId}/target-market-preview`);
}

export function getDestinationBinding(channelId: string) {
  return request<DestinationBinding | null>(`/channels/${channelId}/destination-binding`);
}

export function researchChannelInitDraft(draftId: string) {
  return request<ChannelContractDraft>(`/channel-init-drafts/${draftId}/research`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function reviewChannelInitDraft(draftId: string, decisions: Array<Record<string, unknown>>, humanNotes?: string) {
  return request<ChannelContractDraft>(`/channel-init-drafts/${draftId}/review`, {
    method: "POST",
    body: JSON.stringify({ decisions, human_notes: humanNotes ?? null })
  });
}

export function compileChannelInitDraft(draftId: string) {
  return request<ChannelInitCompileResult>(`/channel-init-drafts/${draftId}/compile`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function getChannelWorkspace(channelId: string) {
  return request<ChannelWorkspace>(`/channels/${channelId}/workspace`);
}

export function getChannelProfileManagement(channelId: string) {
  return request<ChannelProfileManagement>(`/channels/${channelId}/profile-management`);
}

export function createChannelProfileDraft(channelId: string) {
  return request<Record<string, unknown>>(`/channels/${channelId}/profile-versions/draft-from-active`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function updateChannelProfileDraft(profileVersionId: string, profileInput: Record<string, unknown>, expectedHash: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/draft`, {
    method: "PUT",
    body: JSON.stringify({ profile_input: profileInput, expected_profile_input_hash: expectedHash })
  });
}

export function validateChannelProfileDraft(profileVersionId: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/validate`, { method: "POST", body: JSON.stringify({}) });
}

export function previewChannelProfileCompile(profileVersionId: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/preview-compile`, { method: "POST", body: JSON.stringify({}) });
}

export function compileChannelProfile(profileVersionId: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/compile`, { method: "POST", body: JSON.stringify({}) });
}

export function submitChannelProfile(profileVersionId: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/submit-for-approval`, { method: "POST", body: JSON.stringify({}) });
}

export function approveChannelProfile(profileVersionId: string, approvalRef: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/approve`, {
    method: "POST",
    body: JSON.stringify({ approval_ref: approvalRef, approved_by: null })
  });
}

export function rejectChannelProfile(profileVersionId: string, reason: string) {
  return request<Record<string, unknown>>(`/profile-versions/${profileVersionId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason, rejected_by: null })
  });
}

export function activatePolicySnapshot(snapshotId: string) {
  return request<Record<string, unknown>>(`/policy-snapshots/${snapshotId}/activate`, { method: "POST", body: JSON.stringify({}) });
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

export function getVideoPackageReview(packageId: string) {
  return request<VideoPackageReview>(`/video-packages/${packageId}/review`);
}

export function getPackagingReviewQueue(packageId: string) {
  return request<PackagingReviewQueue>(`/video-packages/${packageId}/packaging-review-queue`);
}

export function buildPackagingReviewQueueFromGates(packageId: string) {
  return request<PackagingReviewQueue>(`/video-packages/${packageId}/packaging-review-queue/build-from-gates`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function approvePackagingProposedPatch(patchId: string, rationale?: string) {
  return request<PackagingPatchApprovalDecision>(`/packaging-proposed-patches/${patchId}/approve`, {
    method: "POST",
    body: JSON.stringify({ decided_by: "operator", rationale: rationale ?? null })
  });
}

export function rejectPackagingProposedPatch(patchId: string, rationale?: string) {
  return request<PackagingPatchApprovalDecision>(`/packaging-proposed-patches/${patchId}/reject`, {
    method: "POST",
    body: JSON.stringify({ decided_by: "operator", rationale: rationale ?? null })
  });
}

export function requestChangesPackagingProposedPatch(patchId: string, rationale?: string) {
  return request<PackagingPatchApprovalDecision>(`/packaging-proposed-patches/${patchId}/request-changes`, {
    method: "POST",
    body: JSON.stringify({ decided_by: "operator", rationale: rationale ?? null })
  });
}

export function applyPackagingProposedPatch(patchId: string) {
  return request<PackagingPatchApplyRun>(`/packaging-proposed-patches/${patchId}/apply`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function rerunPackagingGates(packageId: string) {
  return request<PackagingGateRerunRecord>(`/video-packages/${packageId}/rerun-packaging-gates`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function applyApprovedChangesAndRecheckPackage(packageId: string) {
  return request<PackagingApplyApprovedChangesResult>(`/video-packages/${packageId}/apply-approved-changes-and-recheck`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function createUploadTaskFromPackage(packageId: string) {
  return request<HumanUploadTask>(`/video-packages/${packageId}/upload-task`, {
    method: "POST",
    body: JSON.stringify({})
  });
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

export function getRuntimeOpsCommandCenter() {
  return request<RuntimeOpsCommandCenter>("/ops/command-center");
}

export function getChannelRuntimeTrace(channelId: string) {
  return request<ChannelRuntimeTrace>(`/channels/${channelId}/runtime-trace`);
}

export function getProjectRuntimeTrace(projectId: string) {
  return request<ChannelRuntimeTrace>(`/video-projects/${projectId}/runtime-trace`);
}

export function getPackageOpsSummary(packageId: string) {
  return request<PackageOpsSummary>(`/video-packages/${packageId}/ops-summary`);
}

export function getUploadedVideoOpsSummary(uploadedVideoId: string) {
  return request<UploadedVideoOpsSummary>(`/uploaded-videos/${uploadedVideoId}/ops-summary`);
}

export function getDiagnosticsQueue() {
  return request<OpsQueue>("/diagnostics/queue");
}

export function getRecoveryQueue() {
  return request<OpsQueue>("/recovery/queue");
}

export function getLearningOpsQueue() {
  return request<OpsQueue>("/learning/queue");
}

export function getMemoryOpsQueue() {
  return request<OpsQueue>("/memory/review-queue/ops");
}

export function getRetrievalManifestOps(manifestId: string) {
  return request<RetrievalManifestOps>(`/retrieval-manifests/${manifestId}`);
}

export function getMemoryInfluenceOps(manifestId: string) {
  return request<MemoryInfluenceOps>(`/memory-influence/${manifestId}`);
}

export function getQualityDeltaOps(qualityDeltaId: string) {
  return request<QualityDeltaOps>(`/quality-delta/${qualityDeltaId}`);
}

export function getProviderCostOps(packageId: string) {
  return request<ProviderCostOps>(`/provider-cost/${packageId}`);
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
      ai_hero_provider: "Google Veo API",
      ai_hero_model_id: "veo-3.1-fast-generate-preview",
      ai_hero_allowed_durations_seconds: [8],
      ai_hero_default_duration_seconds: 8,
      ai_hero_audio: true,
      ai_hero_allowed_use: ["hero_shot", "hard_to_find_visual"],
      ai_hero_forbidden_use: ["data_diagram", "workflow_chart", "factual_evidence_visualization"],
      renderer: "NativeFFmpegRenderer",
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
