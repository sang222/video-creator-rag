"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { EmptyStateCard } from "@/components/cockpit";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  activatePolicySnapshot,
  approveChannelProfile,
  compileChannelProfile,
  createChannelProfileDraft,
  getChannelProfileManagement,
  previewChannelProfileCompile,
  queryKeys,
  rejectChannelProfile,
  submitChannelProfile,
  updateChannelProfileDraft,
  validateChannelProfileDraft
} from "@/lib/api";
import type { ChannelProfileVersion } from "@/lib/types";

export function ChannelProfileManager({ channelId }: { channelId: string }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.channelProfileManagement(channelId),
    queryFn: () => getChannelProfileManagement(channelId)
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [approvalRef, setApprovalRef] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const versions = useMemo(() => query.data?.versions ?? [], [query.data?.versions]);
  const selected = useMemo(
    () => versions.find((item) => item.id === selectedId) ?? versions[0] ?? null,
    [selectedId, versions]
  );
  const [nativeMaximum, setNativeMaximum] = useState("0.70");
  const [heroClips, setHeroClips] = useState("1");

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.channelProfileManagement(channelId) });
    await client.invalidateQueries({ queryKey: queryKeys.channelWorkspace(channelId) });
  };
  const action = useMutation({
    mutationFn: async (operation: () => Promise<Record<string, unknown>>) => operation(),
    onSuccess: async (value) => {
      setResult(value);
      setError(null);
      await refresh();
    },
    onError: (value: Error) => setError(value.message)
  });
  const run = (operation: () => Promise<Record<string, unknown>>) => {
    setResult(null);
    setError(null);
    action.mutate(operation);
  };

  if (query.isLoading) return <Panel><p className="text-sm text-muted-foreground">Đang tải các phiên bản hồ sơ…</p></Panel>;
  if (query.isError) return <Panel><p className="text-sm text-rose-200">Không tải được hồ sơ kênh: {query.error.message}</p></Panel>;
  if (!selected) {
    return <EmptyStateCard title="Chưa có phiên bản hồ sơ" description="Tạo hồ sơ kênh và compile snapshot trước khi tạo dự án production." />;
  }

  return (
    <div className="space-y-4">
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">Phiên bản hồ sơ kênh</h2>
            <p className="mt-2 text-sm text-muted-foreground">Chỉ draft mới được sửa. Kích hoạt chỉ ảnh hưởng dự án tạo sau đó; dự án cũ giữ nguyên snapshot.</p>
          </div>
          <Button
            variant="primary"
            disabled={action.isPending || versions.some((item) => item.status === "draft")}
            onClick={() => run(() => createChannelProfileDraft(channelId))}
          >
            Tạo draft từ bản đang chạy
          </Button>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {versions.map((version) => (
            <button
              key={version.id}
              type="button"
              className={`rounded-md border p-3 text-left ${selected.id === version.id ? "border-primary bg-primary/5" : "border-border bg-muted/20"}`}
              onClick={() => {
                setSelectedId(version.id);
                const policy = readPolicy(version);
                setNativeMaximum(String(policy.nativeMaximum));
                setHeroClips(String(policy.heroClips));
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">Phiên bản {version.version}</span>
                <StatusBadge value={version.is_active ? "ACTIVE" : version.status} />
              </div>
              <p className="mt-2 text-xs text-muted-foreground">Khả năng: {friendlyCapability(version.capability_status)}</p>
            </button>
          ))}
        </div>
      </Panel>

      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold">Chính sách được phép chỉnh</h3>
            <p className="mt-1 text-sm text-muted-foreground">Các tỷ lệ chỉ là dải lập kế hoạch; không tạo quota Pexels/Veo.</p>
          </div>
          <StatusBadge value={selected.status} />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="text-muted-foreground">Trần dải native explanatory</span>
            <input
              type="number"
              min="0.5"
              max="1"
              step="0.01"
              disabled={selected.status !== "draft"}
              value={nativeMaximum}
              onChange={(event) => setNativeMaximum(event.target.value)}
              className="w-full rounded-md border border-border bg-muted px-3 py-2"
            />
          </label>
          <label className="space-y-2 text-sm">
            <span className="text-muted-foreground">Tối đa Veo hero clip/video</span>
            <input
              type="number"
              min="0"
              max="4"
              step="1"
              disabled={selected.status !== "draft"}
              value={heroClips}
              onChange={(event) => setHeroClips(event.target.value)}
              className="w-full rounded-md border border-border bg-muted px-3 py-2"
            />
          </label>
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          {selected.status === "draft" ? (
            <>
              <Button disabled={action.isPending} onClick={() => run(() => saveDraft(selected, nativeMaximum, heroClips))}>Lưu draft</Button>
              <Button disabled={action.isPending} onClick={() => run(() => validateChannelProfileDraft(selected.id))}>Kiểm tra draft</Button>
              <Button disabled={action.isPending} onClick={() => run(() => previewChannelProfileCompile(selected.id))}>Xem trước compile</Button>
              <Button variant="primary" disabled={action.isPending} onClick={() => run(() => compileChannelProfile(selected.id))}>Compile snapshot</Button>
            </>
          ) : null}
          {selected.status === "compiled" ? (
            <Button variant="primary" disabled={action.isPending} onClick={() => run(() => submitChannelProfile(selected.id))}>Gửi duyệt</Button>
          ) : null}
          {selected.status === "pending_approval" ? (
            <>
              <input
                value={approvalRef}
                onChange={(event) => setApprovalRef(event.target.value)}
                placeholder="approval ref của operator"
                className="min-w-72 rounded-md border border-border bg-muted px-3 py-2 text-sm"
              />
              <Button variant="primary" disabled={action.isPending || !approvalRef.trim()} onClick={() => run(() => approveChannelProfile(selected.id, approvalRef.trim()))}>Duyệt phiên bản</Button>
              <Button disabled={action.isPending} onClick={() => run(() => rejectChannelProfile(selected.id, "Operator rejected from profile management."))}>Từ chối</Button>
            </>
          ) : null}
          {selected.status === "approved" && selected.latest_snapshot_id ? (
            <Button variant="primary" disabled={action.isPending} onClick={() => run(() => activatePolicySnapshot(selected.latest_snapshot_id!))}>
              {selected.version < Math.max(...versions.map((item) => item.version)) ? "Rollback về phiên bản này" : "Kích hoạt cho dự án mới"}
            </Button>
          ) : null}
        </div>
        {selected.capability_blockers.length ? (
          <div className="mt-4 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
            Còn {selected.capability_blockers.length} blocker khả năng. Xem chi tiết kỹ thuật bên dưới.
          </div>
        ) : null}
        {error ? <div className="mt-4 rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">Thao tác thất bại. Kiểm tra dữ liệu draft và quyền duyệt.</div> : null}
        {result ? <div className="mt-4 rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">Thao tác hoàn tất; read model đã được làm mới.</div> : null}
        <details className="mt-5 text-xs text-muted-foreground">
          <summary className="cursor-pointer">Chi tiết kỹ thuật</summary>
          <pre className="mt-3 overflow-x-auto whitespace-pre-wrap">{JSON.stringify({ profile_id: selected.id, profile_hash: selected.profile_input_hash, snapshot_id: selected.latest_snapshot_id, snapshot_hash: selected.latest_snapshot_hash, blockers: selected.capability_blockers, last_error: error, last_result: result }, null, 2)}</pre>
        </details>
      </Panel>
    </div>
  );
}

function readPolicy(version: ChannelProfileVersion) {
  const input = version.profile_input as { channel_policy?: Record<string, unknown> };
  const policy = input.channel_policy as {
    channel_visual_strategy_profile?: { native_explanatory_target_range?: { maximum?: number } };
    provider_usage_policy?: { google_veo?: { max_hero_clips_per_video?: number } };
  } | undefined;
  return {
    nativeMaximum: policy?.channel_visual_strategy_profile?.native_explanatory_target_range?.maximum ?? 0.7,
    heroClips: policy?.provider_usage_policy?.google_veo?.max_hero_clips_per_video ?? 1
  };
}

function saveDraft(version: ChannelProfileVersion, nativeMaximum: string, heroClips: string) {
  const input = structuredClone(version.profile_input) as Record<string, unknown> & {
    channel_policy: {
      channel_visual_strategy_profile: { native_explanatory_target_range: { maximum: number } };
      provider_usage_policy: { google_veo: { max_hero_clips_per_video: number } };
      budget_policy: { max_veo_clips_per_video: number };
    };
  };
  const maximum = Number(nativeMaximum);
  const clips = Number(heroClips);
  input.channel_policy.channel_visual_strategy_profile.native_explanatory_target_range.maximum = maximum;
  input.channel_policy.provider_usage_policy.google_veo.max_hero_clips_per_video = clips;
  input.channel_policy.budget_policy.max_veo_clips_per_video = clips;
  return updateChannelProfileDraft(version.id, input, version.profile_input_hash);
}

function friendlyCapability(value: string) {
  if (value === "PASS") return "Đủ điều kiện compile";
  if (value === "BLOCKED") return "Còn blocker";
  return "Chưa compile";
}
