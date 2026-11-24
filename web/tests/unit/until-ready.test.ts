/**
 * The app shell loads the package once per RFQ. While the analysis runs the backend
 * answers 409 `not_ready` (SPEC §14), so the load waits and asks again instead of
 * leaving the screen with "문서 정보를 불러오지 못함" and no file list.
 */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/rfq/provider";
import { untilReady } from "@/lib/rfq/until-ready";

const notReady = () => new ApiError("document", 409, "not_ready", "아직 분석 중입니다.");

describe("untilReady", () => {
  it("asks again while the analysis is running, then returns the result", async () => {
    let calls = 0;
    const load = async () => {
      calls += 1;
      if (calls < 3) throw notReady();
      return "ok";
    };
    await expect(untilReady(load, new AbortController().signal, 1)).resolves.toBe("ok");
    expect(calls).toBe(3);
  });

  it("does not retry other errors", async () => {
    let calls = 0;
    const failed = new ApiError("document", 409, "job_failed", "분석이 중단됐습니다.");
    const load = async () => {
      calls += 1;
      throw failed;
    };
    await expect(untilReady(load, new AbortController().signal, 1)).rejects.toBe(failed);
    expect(calls).toBe(1);
  });

  it("stops when the screen is left", async () => {
    const controller = new AbortController();
    let calls = 0;
    const load = async () => {
      calls += 1;
      if (calls === 2) controller.abort();
      throw notReady();
    };
    await expect(untilReady(load, controller.signal, 1)).rejects.toThrow();
    expect(calls).toBe(2);
  });
});
