/**
 * Plays the MP3 the server made for a verified answer, and tells how loud it is for the sphere. One sound at a
 * time; `stop` ends it and settles the pending `play`. Browser only.
 */
import { inputLevel, smoothInputLevel } from "./level";

export interface Player {
  /** Resolves when the sound has played to its end or was stopped; rejects when it cannot be played. */
  play(bytes: Uint8Array<ArrayBuffer>): Promise<void>;
  stop(): void;
  /** Smoothed playback level, 0..1. */
  level(): number;
  /** Frees the audio context. The player is not used afterwards. */
  dispose(): void;
}

const FFT_SIZE = 1024;
const MS_PER_SECOND = 1000;

export function createPlayer(): Player {
  let audio: HTMLAudioElement | null = null;
  let url: string | null = null;
  let context: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let samples: Uint8Array<ArrayBuffer> | null = null;
  let settle: (() => void) | null = null;
  let level = 0;
  let sampledAt = 0;

  function clear(): void {
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
    }
    audio = null;
    if (url) URL.revokeObjectURL(url);
    url = null;
    level = 0;
    const done = settle;
    settle = null;
    done?.();
  }

  function meter(element: HTMLAudioElement): void {
    try {
      context ??= new AudioContext();
      analyser = context.createAnalyser();
      analyser.fftSize = FFT_SIZE;
      samples = new Uint8Array(analyser.fftSize);
      const source = context.createMediaElementSource(element);
      source.connect(analyser);
      analyser.connect(context.destination);
      void context.resume();
    } catch {
      // No meter: the sound still plays straight from the element.
      analyser = null;
    }
  }

  return {
    play(bytes) {
      clear();
      const element = new Audio();
      audio = element;
      url = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
      element.src = url;
      meter(element);
      return new Promise<void>((resolve, reject) => {
        settle = resolve;
        element.onended = clear;
        element.onerror = () => {
          settle = null;
          clear();
          reject(new Error("playback failed"));
        };
        element.play().catch((e: unknown) => {
          settle = null;
          clear();
          reject(e);
        });
      });
    },

    stop: clear,

    level() {
      if (!audio || !analyser || !samples) return 0;
      const now = performance.now();
      analyser.getByteTimeDomainData(samples);
      level = smoothInputLevel(level, inputLevel(samples), (now - sampledAt) / MS_PER_SECOND);
      sampledAt = now;
      return level;
    },

    dispose() {
      clear();
      void context?.close().catch(() => undefined);
      context = null;
    },
  };
}
