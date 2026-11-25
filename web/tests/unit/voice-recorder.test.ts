/**
 * The recorder when the browser ends the recording by itself: the microphone and the audio context are released
 * and the caller is told, so the screen does not stay on "recording". Browser APIs are fakes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRecorder } from "@/lib/voice/recorder";

class FakeTrack {
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

class FakeMedia {
  static last: FakeMedia | null = null;
  static isTypeSupported = () => true;
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    FakeMedia.last = this;
  }
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}

class FakeContext {
  static last: FakeContext | null = null;
  closed = false;
  constructor() {
    FakeContext.last = this;
  }
  createAnalyser() {
    return { fftSize: 0, getByteTimeDomainData: () => undefined };
  }
  createMediaStreamSource() {
    return { connect: () => undefined };
  }
  close() {
    this.closed = true;
    return Promise.resolve();
  }
}

let track: FakeTrack;

beforeEach(() => {
  track = new FakeTrack();
  FakeMedia.last = null;
  FakeContext.last = null;
  vi.stubGlobal("MediaRecorder", FakeMedia);
  vi.stubGlobal("AudioContext", FakeContext);
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) } });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const options = () => ({ maxSeconds: 60, onLimit: vi.fn(), onInterrupted: vi.fn() });

describe("createRecorder, when the browser ends the recording itself", () => {
  it("releases the microphone and the audio context on a recorder error, and tells the caller", async () => {
    const opts = options();
    const recorder = createRecorder(opts);
    await recorder.start();
    FakeMedia.last?.onerror?.();
    expect(track.stopped).toBe(true);
    expect(FakeContext.last?.closed).toBe(true);
    expect(opts.onInterrupted).toHaveBeenCalledTimes(1);
  });

  it("does the same when the recorder stops without being asked to", async () => {
    const opts = options();
    const recorder = createRecorder(opts);
    await recorder.start();
    FakeMedia.last?.onstop?.();
    expect(track.stopped).toBe(true);
    expect(FakeContext.last?.closed).toBe(true);
    expect(opts.onInterrupted).toHaveBeenCalledTimes(1);
  });

  it("stays quiet when the user stops it or cancels it", async () => {
    const stopped = options();
    const a = createRecorder(stopped);
    await a.start();
    const audio = a.stop();
    await audio;
    expect(track.stopped).toBe(true);
    expect(stopped.onInterrupted).not.toHaveBeenCalled();

    track = new FakeTrack();
    const cancelled = options();
    const b = createRecorder(cancelled);
    await b.start();
    b.cancel();
    expect(track.stopped).toBe(true);
    expect(cancelled.onInterrupted).not.toHaveBeenCalled();
  });

  it("reports an interruption once, however many events follow", async () => {
    const opts = options();
    const recorder = createRecorder(opts);
    await recorder.start();
    FakeMedia.last?.onerror?.();
    FakeMedia.last?.onstop?.();
    expect(opts.onInterrupted).toHaveBeenCalledTimes(1);
  });
});
