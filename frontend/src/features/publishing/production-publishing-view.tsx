"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ShieldCheck } from "lucide-react";

import { EmptyStateCard, PageHeader } from "@/components/cockpit";
import { LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { FinalReviewSurface } from "@/features/production/final-review-surface";
import { ManualPublishSurface } from "@/features/production/manual-publish-surface";
import { PrivatePublicationSurface } from "@/features/production/private-publication-surface";
import { ProductionCockpitCard } from "@/features/production/production-cockpit-card";
import {
  correctManualPublishConfirmation,
  decideFinalVideo,
  getProductionCockpit,
  queryKeys,
  reviewYoutubePrivateStage,
  startManualUpload,
  submitManualPublishConfirmation
} from "@/lib/api";
import type { ManualPublishConfirmationInput } from "@/lib/types";

type PublishAction = "start" | "confirm" | "correct";

export function ProductionPublishingView() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const query = useQuery({
    queryKey: queryKeys.productionCockpit(),
    queryFn: () => getProductionCockpit(),
    retry: false
  });
  const refresh = async () => {
    const projectId = query.data?.next_video?.project_id;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.productionCockpit() }),
      projectId
        ? queryClient.invalidateQueries({
            queryKey: queryKeys.productionCockpit(projectId)
          })
        : Promise.resolve(),
      queryClient.invalidateQueries({ queryKey: queryKeys.commandCenter })
    ]);
  };
  const decisionMutation = useMutation({
    mutationFn: ({
      candidateId,
      decision,
      warningCodes
    }: {
      candidateId: string;
      decision: "UPLOAD" | "DO_NOT_UPLOAD";
      warningCodes: string[];
    }) => decideFinalVideo(candidateId, decision, warningCodes),
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.decision === "UPLOAD"
          ? "Đã ghi UPLOAD; bước tiếp theo vẫn là thao tác thủ công ngoài VCOS."
          : "Đã ghi DO_NOT_UPLOAD; VCOS không tạo task upload."
      );
      await refresh();
    },
    onError: () =>
      setNotice("Chưa thể ghi quyết định. Không có quyết định ngầm nào được áp dụng.")
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
      if (!input) throw new Error("CONFIRMATION_INPUT_REQUIRED");
      if (action === "correct") {
        return correctManualPublishConfirmation(publish, input);
      }
      return submitManualPublishConfirmation(publish.task_id, publish, input);
    },
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.action === "start"
          ? "Đã ghi nhận bắt đầu upload thủ công; VCOS không gọi nền tảng."
          : variables.action === "correct"
            ? "Đã lưu correction có lịch sử."
            : "Đã gửi xác nhận để đối chiếu."
      );
      await refresh();
    },
    onError: () =>
      setNotice("Chưa thể ghi nhận kết quả publish; mismatch vẫn được giữ nguyên.")
  });
  const privateStageMutation = useMutation({
    mutationFn: ({
      disposition,
      reason
    }: {
      disposition: "REJECT" | "NEEDS_RERENDER";
      reason: string;
    }) => {
      const stage = query.data?.youtube_private_stage;
      if (!stage) throw new Error("YOUTUBE_PRIVATE_STAGE_NOT_READY");
      return reviewYoutubePrivateStage(stage.stage_id, disposition, reason);
    },
    onSuccess: async (_result, variables) => {
      setNotice(
        variables.disposition === "REJECT"
          ? "Đã ghi nhận reject. Stage cũ vẫn giữ nguyên làm bằng chứng lịch sử."
          : "Đã ghi nhận needs rerender. Hãy tạo replacement package mới theo workflow được quản lý."
      );
      await refresh();
    },
    onError: () =>
      setNotice("Chưa thể ghi nhận disposition. Stage và lịch sử vẫn được giữ nguyên.")
  });

  if (query.isLoading) {
    return (
      <div className="p-4 md:p-8">
        <LoadingState label="Đang tải video chờ publish" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="space-y-5 p-4 md:p-8">
        <PageHeader
          title="Xem video & publish thủ công"
          subtitle="Read-model production chưa sẵn sàng. Trang này không quay về luồng package/gate cũ và không tạo upload tự động."
          breadcrumbs={[
            { label: "Trung tâm", href: "/" },
            { label: "Publish thủ công" }
          ]}
        />
        <EmptyStateCard
          title="Chưa đọc được video chờ publish"
          description="Khi dịch vụ production hoạt động, video cuối và task upload thủ công sẽ xuất hiện tại đây. Không cần dùng CLI hoặc sửa database."
          actions={[{ label: "Xem dự án", href: "/projects" }]}
        />
      </div>
    );
  }

  const cockpit = query.data;
  const privatePublicationMode = Boolean(
    cockpit.private_publication_mode || cockpit.youtube_private_stage
  );
  const projectId = cockpit.next_video?.project_id;
  const warningCodes = technicalStrings(
    cockpit.final_review?.technical_appendix.warning_codes
  );

  return (
    <div className="space-y-8 p-4 md:p-8">
      <PageHeader
        title={
          privatePublicationMode
            ? "Xem YouTube PRIVATE & cutover PUBLIC"
            : "Xem video & publish thủ công"
        }
        subtitle={cockpit.safety_notice}
        breadcrumbs={[
          { label: "Trung tâm", href: "/" },
          {
            label: privatePublicationMode
              ? "YouTube PRIVATE staging"
              : "Publish thủ công"
          }
        ]}
        primaryAction={
          projectId ? (
            <Button asChild>
              <Link href={`/projects/${projectId}/production`}>
                Xem toàn bộ tiến độ
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            </Button>
          ) : null
        }
      />

      {notice ? (
        <Panel className="border-primary/35 bg-primary/5" role="status">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 text-primary" size={18} aria-hidden="true" />
            <p className="text-sm leading-6">{notice}</p>
          </div>
        </Panel>
      ) : null}

      <ProductionCockpitCard nextVideo={cockpit.next_video} />

      {cockpit.final_review ? (
        <FinalReviewSurface
          busyDecision={
            privatePublicationMode
              ? null
              : decisionMutation.variables?.decision ?? null
          }
          onDecision={
            privatePublicationMode
              ? undefined
              : (decision) =>
                  decisionMutation.mutate({
                    candidateId: cockpit.final_review!.candidate_id,
                    decision,
                    warningCodes
                  })
          }
          privateStage={privatePublicationMode}
          review={cockpit.final_review}
        />
      ) : (
        <EmptyStateCard
          title="Chưa có video cuối để quyết định"
          description="MP4 chỉ xuất hiện sau khi render, QC và archive verification hoàn tất. Người vận hành không cần duyệt từng gate trước đó."
          actions={
            projectId
              ? [
                  {
                    label: "Xem tiến độ sản xuất",
                    href: `/projects/${projectId}/production`,
                    variant: "primary"
                  }
                ]
              : [{ label: "Xem dự án", href: "/projects" }]
          }
        />
      )}

      {cockpit.youtube_private_stage ? (
        <PrivatePublicationSurface
          busy={privateStageMutation.variables?.disposition ?? null}
          onReview={(disposition, reason) =>
            privateStageMutation.mutate({ disposition, reason })
          }
          stage={cockpit.youtube_private_stage}
        />
      ) : privatePublicationMode ? (
        <Panel className="border-primary/35">
          <h2 className="text-lg font-semibold">YouTube PRIVATE staging đang chờ</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            QC PASS đã chuyển package vào luồng staging tự động. Chưa có hành động
            upload hoặc quyết định UPLOAD thủ công cần thực hiện.
          </p>
        </Panel>
      ) : null}

      {cockpit.manual_publish && !privatePublicationMode ? (
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
          publish={cockpit.manual_publish}
        />
      ) : null}
    </div>
  );
}

function technicalStrings(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}
