/**
 * Records the question (docs/design/immersive-ui.md §6): `MediaRecorder` with a format chosen at run time, a hard
 * stop at `maxSeconds`, and the input level through an analyser. Browser only; the decisions it makes (format,
 * level, phases) live in pure files next to it and are tested there.
 */
import { VoiceUnsupportedError } from "./errors";
import { inputLevel, smoothInputLevel } from "./level";
import { pickMimeType } from "./mime";

/**
 * Enough for speech, and it bounds the upload: a browser's default can be eight times this, which would bring
 * sixty seconds close to the server's 2 MiB limit. At this rate a full recording is about 240 kB.
 */
const SPEECH_BITS_PER_SECOND = 32_000;

export interface RecordedAudio {
  blob: Blob;
  durationMs: number;
}

export interface Recorder {
  /** Asks for the microphone and starts. Rejects like `getUserMedia`, or with `VoiceUnsupportedError`. */
  start(): Promise<void>;
  /** Stops, releases the microphone and returns what was recorded. */
  stop(): Promise<RecordedAudio>;
  /** Releases everything and drops the recording. Also ends a `start` still waiting for the permission. */
  cancel(): void;
  /** Smoothed input level, 0..1. Call it about once per frame. */
  level(): number;
  elapsedMs(): number;
}

export interface RecorderOptions {
  maxSeconds: number;
  /** The hard limit was reached: the caller finishes the recording the way it does when the user stops it. */
  onLimit: () => void;
  /**
   * The browser ended the recording by itself (a recorder error, or a stop nobody asked for). The microphone and
   * the audio context are already released; the caller leaves its "recording" state.
   */
  onInterrupted: () => void;
}

const FFT_SIZE = 1024;
const MS_PER_SECOND = 1000;
const FALLBACK_TYPE = "audio/webm";

const abortError = () => new DOMException("The recording was cancelled.", "AbortError");

function isRecordingSupported(): boolean {
  return typeof MediaRecorder !== "undefined" && typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
}

export function createRecorder({ maxSeconds, onLimit, onInterrupted }: RecorderOptions): Recorder {
  let stream: MediaStream | null = null;
  let media: MediaRecorder | null = null;
  let context: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let samples: Uint8Array<ArrayBuffer> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let chunks: Blob[] = [];
  let isCancelled = false;
  let isInterrupted = false;
  let startedAt = 0;
  let endedAt: number | null = null;
  let level = 0;
  let sampledAt = 0;

  function release(): void {
    clearTimeout(timer);
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    void context?.close().catch(() => undefined);
    context = null;
    analyser = null;
  }

  /** `onerror` or an `onstop` that `stop()` and `cancel()` did not ask for. */
  function interrupt(): void {
    if (isInterrupted || isCancelled) return;
    isInterrupted = true;
    endedAt ??= performance.now();
    release();
    onInterrupted();
  }

  function listen(source: MediaStream): void {
    try {
      context = new AudioContext();
      analyser = context.createAnalyser();
      analyser.fftSize = FFT_SIZE;
      samples = new Uint8Array(analyser.fftSize);
      context.createMediaStreamSource(source).connect(analyser);
    } catch {
      // No meter: the recording works without it and the sphere stays calm.
      analyser = null;
    }
  }

  return {
    async start() {
      if (!isRecordingSupported()) throw new VoiceUnsupportedError();
      const opened = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (isCancelled) {
        opened.getTracks().forEach((track) => track.stop());
        throw abortError();
      }
      stream = opened;
      const type = pickMimeType((t) => MediaRecorder.isTypeSupported(t));
      const options = { audioBitsPerSecond: SPEECH_BITS_PER_SECOND };
      media = new MediaRecorder(opened, type ? { ...options, mimeType: type } : options);
      media.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      media.onerror = interrupt;
      media.onstop = interrupt;
      listen(opened);
      media.start();
      startedAt = performance.now();
      timer = setTimeout(onLimit, maxSeconds * MS_PER_SECOND);
    },

    stop() {
      const recorder = media;
      if (!recorder || recorder.state === "inactive") return Promise.reject(new Error("not recording"));
      return new Promise<RecordedAudio>((resolve, reject) => {
        recorder.onstop = () => {
          const durationMs = (endedAt ??= performance.now()) - startedAt;
          const blob = new Blob(chunks, { type: recorder.mimeType || FALLBACK_TYPE });
          release();
          resolve({ blob, durationMs });
        };
        recorder.onerror = () => {
          release();
          reject(new Error("recording failed"));
        };
        endedAt = performance.now();
        clearTimeout(timer);
        recorder.stop();
      });
    },

    cancel() {
      isCancelled = true;
      if (media) {
        media.onstop = null;
        media.onerror = null;
        if (media.state !== "inactive") media.stop();
      }
      chunks = [];
      release();
    },

    level() {
      if (!analyser || !samples || endedAt !== null) return 0;
      const now = performance.now();
      analyser.getByteTimeDomainData(samples);
      level = smoothInputLevel(level, inputLevel(samples), (now - sampledAt) / MS_PER_SECOND);
      sampledAt = now;
      return level;
    },

    elapsedMs() {
      return startedAt === 0 ? 0 : (endedAt ?? performance.now()) - startedAt;
    },
  };
}
