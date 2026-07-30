"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, ShieldCheck } from "lucide-react";

import { ActionHintCard, EmptyStateCard, PageHeader } from "@/components/cockpit";
import { LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  cancelProductionWorkflow,
  correctManualPublishConfirmation,
  decideFinalVideo,
  getProductionCockpit,
  queryKeys,
  resumeProductionWorkflow,
  startManualUpload,
  startProjectProduction,
  submitManualPublishConfirmation,
  verifyManualPublishConfirmation
} from "@/lib/api";
import type { ManualPublishConfirmationInput } from "@/lib/types";
import { FinalReviewSurface } from "./final-review-surface";
import { ManualPublishSurface } from "./manual-publish-surface";
import { ProductionCockpitCard } from "./production-cockpit-card";
import { ProductionProgressSurface } from "./production-progress";

type ProgressAction = "start" | "resume" | "cancel";
type FinalDecision = "UPLOAD" | "DO_NOT_UPLOAD";
type PublishAction = "start" | "confirm" | "correct" | "verify";

export function ProjectProductionView({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const query = useQuery({
    queryKey: queryKeys.productionCockpit(projectId),
    queryFn: () => getProductionCockpit(projectId),
    retry: false
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.productionCockpit(projectId)
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.productionCockpit() }),
      queryClient.invalidateQueries({ queryKey: queryKeys.commandCenter })
    ]);
  };
  const progressMutation = useMutation({
    mutationFn: async ({
      action,
      workflowRunId
    }: {
      action: ProgressAction;
      workflowRunId?: string;
    }) => {
      const companyId = technicalString(query.data?.technical_appendix.company_id);
      if (!companyId) throw new Error("COMPANY_SCOPE_REQUIRED");
      if (action === "start") return startProjectProduction(projectId, companyId);
      if (!workflowRunId) throw new Error("WORKFLOW_RUN_REQUIRED");
      if (action === "resume") {
        return resumeProductionWorkflow(workflowRunId, companyId);
      }
      return cancelProductionWorkflow(workflowRunId, companyId);
    },
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.action === "cancel"
          ? "Đã gửi yêu cầu dừng an toàn."
          : variables.action === "resume"
            ? "Đã tiếp tục từ checkpoint bền vững."
            : "Đã bắt đầu luồng sản xuất."
      );
      await refresh();
    },
    onError: () => {
      setNotice(
        "Chưa thể cập nhật luồng sản xuất. Kiểm tra trạng thái hiện tại rồi thử lại."
      );
    }
  });
  const finalDecisionMutation = useMutation({
    mutationFn: ({
      candidateId,
      decision,
      warningCodes
    }: {
      candidateId: string;
      decision: FinalDecision;
      warningCodes: string[];
    }) => decideFinalVideo(candidateId, decision, warningCodes),
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.decision === "UPLOAD"
          ? "Đã ghi quyết định UPLOAD. VCOS chỉ chuẩn bị task upload thủ công."
          : "Đã ghi quyết định DO_NOT_UPLOAD. VCOS sẽ không tạo task upload."
      );
      await refresh();
    },
    onError: () => {
      setNotice(
        "Chưa thể ghi quyết định video cuối. Quyết định trước đó, nếu có, vẫn được giữ nguyên."
      );
    }
  });
  const publishMutation = useMutation({
    mutationFn: async ({
      action,
      input
    }: {
      action: PublishAction;
      input?: ManualPublishConfirmationInput;
    }) => {
      const publish = query.data?.manual_publish;
      if (!publish) throw new Error("MANUAL_PUBLISH_NOT_READY");
      if (action === "start") return startManualUpload(publish.task_id, publish);
      if (action === "verify") {
        return verifyManualPublishConfirmation(publish);
      }
      if (!input) throw new Error("CONFIRMATION_INPUT_REQUIRED");
      if (action === "correct") {
        return correctManualPublishConfirmation(publish, input);
      }
      return submitManualPublishConfirmation(publish.task_id, publish, input);
    },
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.action === "start"
          ? "Đã ghi nhận bắt đầu upload thủ công. VCOS không gửi file lên nền tảng."
          : variables.action === "verify"
            ? "Đã xác minh dữ liệu quan sát và tạo UploadedVideo có lineage đầy đủ."
          : variables.action === "correct"
            ? "Đã lưu correction và giữ lịch sử xác nhận."
            : "Đã ghi xác nhận upload để đối chiếu."
      );
      await refresh();
    },
    onError: () => {
      setNotice(
        "Chưa thể ghi nhận thao tác publish. VCOS không tự sửa hoặc bỏ qua mismatch."
      );
    }
  });

  if (query.isLoading) {
    return (
      <div className="p-4 md:p-8">
        <LoadingState label="Đang tải tiến độ sản xuất" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="space-y-5 p-4 md:p-8">
        <PageHeader
          title="Tiến độ sản xuất"
          subtitle="Read-model sản xuất chưa sẵn sàng. Không có thao tác nền nào được chạy từ trạng thái lỗi này."
          breadcrumbs={[
            { label: "Dự án", href: "/projects" },
            { label: "Tiến độ sản xuất" }
          ]}
        />
        <ActionHintCard
          title="Chưa đọc được dự án"
          body="Kiểm tra dịch vụ production và quyền truy cập, sau đó tải lại trang. Không cần chạy CLI hoặc sửa trực tiếp database."
          href="/projects"
          actionLabel="Quay lại dự án"
        />
      </div>
    );
  }

  const cockpit = query.data;
  const nextVideo = cockpit.next_video;
  const warningCodes = technicalStrings(
    cockpit.final_review?.technical_appendix.warning_codes
  );

  return (
    <div className="space-y-8 p-4 md:p-8">
      <PageHeader
        title={nextVideo?.title ?? "Tiến độ sản xuất"}
        subtitle={cockpit.safety_notice}
        breadcrumbs={[
          { label: "Dự án", href: "/projects" },
          { label: nextVideo?.title ?? "Tiến độ sản xuất" }
        ]}
      />

      {notice ? (
        <Panel className="border-primary/35 bg-primary/5" role="status">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 text-primary" size={18} aria-hidden="true" />
            <p className="text-sm leading-6">{notice}</p>
          </div>
        </Panel>
      ) : null}

      <ProductionCockpitCard nextVideo={nextVideo} />

      {cockpit.progress ? (
        <ProductionProgressSurface
          busyAction={progressMutation.variables?.action ?? null}
          onAction={(action) =>
            progressMutation.mutate({
              action,
              workflowRunId: cockpit.progress?.workflow_run_id
            })
          }
          progress={cockpit.progress}
        />
      ) : nextVideo ? (
        <Panel className="border-primary/35">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">Sẵn sàng bắt đầu sản xuất</h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                VCOS sẽ dùng admission, assignment và duration contract v2 đã chốt. Không
                cần duyệt từng gate trước render.
              </p>
            </div>
            <Button
              disabled={progressMutation.isPending}
              onClick={() => progressMutation.mutate({ action: "start" })}
              variant="primary"
            >
              <Play size={16} aria-hidden="true" />
              {progressMutation.isPending ? "Đang bắt đầu..." : "Bắt đầu sản xuất"}
            </Button>
          </div>
        </Panel>
      ) : null}

      {cockpit.final_review ? (
        <FinalReviewSurface
          busyDecision={
            finalDecisionMutation.variables?.decision ?? null
          }
          onDecision={(decision) =>
            finalDecisionMutation.mutate({
              candidateId: cockpit.final_review!.candidate_id,
              decision,
              warningCodes
            })
          }
          review={cockpit.final_review}
        />
      ) : (
        <EmptyStateCard
          title="Video cuối chưa sẵn sàng"
          description="VCOS đang tiếp tục các bước kỹ thuật. Người vận hành chưa cần duyệt package hoặc gate; khi MP4 đã qua QC và archive verification, hai quyết định UPLOAD / DO_NOT_UPLOAD sẽ xuất hiện."
        />
      )}

      {cockpit.manual_publish ? (
        <ManualPublishSurface
          busyAction={publishMutation.variables?.action ?? null}
          expectedDurationSeconds={cockpit.final_review?.media.duration_seconds}
          onConfirm={(input) =>
            publishMutation.mutate({ action: "confirm", input })
          }
          onCorrect={(input) =>
            publishMutation.mutate({ action: "correct", input })
          }
          onStart={() => publishMutation.mutate({ action: "start" })}
          onVerify={() => publishMutation.mutate({ action: "verify" })}
          publish={cockpit.manual_publish}
        />
      ) : cockpit.final_review?.decision === "UPLOAD" ? (
        <EmptyStateCard
          title="Đang chuẩn bị task upload thủ công"
          description="Quyết định UPLOAD đã được ghi nhận. Task chỉ xuất hiện sau khi lineage video cuối, checksum và đích publish được bind đầy đủ."
        />
      ) : null}
    </div>
  );
}

function technicalStrings(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function technicalString(value: unknown) {
  return typeof value === "string" && value.length ? value : null;
}
