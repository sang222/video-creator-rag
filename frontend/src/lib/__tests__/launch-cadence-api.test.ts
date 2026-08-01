import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  evaluateCadence,
  getLaunchCadenceDashboard,
  pauseLaunchCadence,
  resumeLaunchCadence
} from "@/lib/api";

const fetchMock = vi.fn();

describe("launch cadence API contract", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({})
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
  });

  it("reads the composite dashboard from the channel-scoped endpoint", async () => {
    await getLaunchCadenceDashboard("channel-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/channels/channel-1/launch-cadence",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it("keeps pause and resume bodies free of identity and production parameters", async () => {
    await pauseLaunchCadence("channel-1");
    await resumeLaunchCadence("channel-1");

    const pauseInit = fetchMock.mock.calls[0][1] as RequestInit;
    const resumeInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(JSON.parse(String(pauseInit.body))).toEqual({
      reason_code: "OPERATOR_PAUSE"
    });
    expect(JSON.parse(String(resumeInit.body))).toEqual({
      reason_code: "OPERATOR_RESUME"
    });
  });

  it("evaluates cadence with only the channel workspace identifier", async () => {
    await evaluateCadence("channel-1");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/cadence/evaluate"
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      channel_workspace_id: "channel-1"
    });
  });
});
