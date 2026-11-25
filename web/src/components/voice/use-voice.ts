"use client";
/**
 * The voice feature of the chat screen: press the microphone to start and again to stop, the transcript goes to
 * `onTranscript`, and a finished answer can be read aloud. The rules are in `lib/voice/state.ts`; this hook only
 * connects them to the recorder, the player and the provider, and cleans up when the screen goes away.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatResponse, MetaResponse } from "@/lib/rfq/contract";
import { rfqApi } from "@/lib/rfq/provider";
import { voiceErrorOf, voiceMessage, type VoiceStep } from "@/lib/voice/errors";
import { createLevelStore, type LevelSource } from "@/lib/voice/level-store";
import { createPlayer, type Player } from "@/lib/voice/player";
import { readReadAloud, writeReadAloud } from "@/lib/voice/prefs";
import { createRecorder, type Recorder } from "@/lib/voice/recorder";
import { speakable } from "@/lib/voice/speakable";
import { IDLE, isCapturing, voiceReducer, type VoiceEvent, type VoiceState } from "@/lib/voice/state";

export type VoiceConfig = MetaResponse["data"]["voice"];

/** The level is sampled this often (ms): the sphere smooths by itself. */
const LEVEL_STEP_MS = 50;
const LEVEL_PRECISION = 50;
const MS_PER_SECOND = 1000;

export interface UseVoiceOptions {
  /** From `/meta`; null while it is not known. Nothing is offered then. */
  config: VoiceConfig | null;
  /** A transcript that is not empty. The screen decides whether to send it or to let the user edit it. */
  onTranscript: (text: string) => void;
}

export interface Voice {
  canListen: boolean;
  canSpeak: boolean;
  state: VoiceState;
  /**
   * Microphone level while recording, playback level while speaking; 0 otherwise. Not React state: only the sphere
   * subscribes, so the page does not re-render with every sample.
   */
  level: LevelSource;
  recordedSeconds: number;
  /** Plain Korean, with what to do next; null when there is no error. */
  errorMessage: string | null;
  readAloud: boolean;
  setReadAloud: (on: boolean) => void;
  /** Press to start, press again to stop and send. */
  toggleMic: () => void;
  /** Escape: drops a recording in progress, or stops the reading. */
  cancel: () => void;
  /** Reads a finished answer if the switch is on. Does nothing while the microphone is in use. */
  speakAnswer: (response: ChatResponse) => void;
  /** For a new question and for leaving the screen. */
  stopSpeaking: () => void;
}

export function useVoice({ config, onTranscript }: UseVoiceOptions): Voice {
  const [state, setState] = useState<VoiceState>(IDLE);
  // Read once at mount. The screen sits behind the sign-in gate and the voice controls appear after /meta, so the
  // server never renders this value.
  const [readAloud, setReadAloudState] = useState(readReadAloud);
  const [recordedSeconds, setRecordedSeconds] = useState(0);
  const [level] = useState(createLevelStore);

  const stateRef = useRef<VoiceState>(IDLE);
  const recorderRef = useRef<Recorder | null>(null);
  const playerRef = useRef<Player | null>(null);
  /** The running transcription request, and the running speech request (not the sound already playing). */
  const transcribeRef = useRef<AbortController | null>(null);
  const speechRef = useRef<AbortController | null>(null);
  const transcriptRef = useRef(onTranscript);
  const configRef = useRef(config);
  const readAloudRef = useRef(readAloud);

  useEffect(() => {
    transcriptRef.current = onTranscript;
    configRef.current = config;
  });

  const send = useCallback((event: VoiceEvent): VoiceState => {
    const next = voiceReducer(stateRef.current, event);
    stateRef.current = next;
    setState(next);
    return next;
  }, []);

  const fail = useCallback(
    (e: unknown, step: VoiceStep) => {
      const kind = voiceErrorOf(e, step);
      if (kind) send({ type: "failed", kind });
    },
    [send],
  );

  const player = useCallback((): Player => (playerRef.current ??= createPlayer()), []);

  const stopSpeaking = useCallback(() => {
    if (stateRef.current.phase !== "speaking") return;
    speechRef.current?.abort();
    speechRef.current = null;
    playerRef.current?.stop();
    send({ type: "speech_ended" });
  }, [send]);

  const finish = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder || stateRef.current.phase !== "recording") return;
    recorderRef.current = null;
    const next = send({ type: "recording_stopped", elapsedMs: recorder.elapsedMs() });
    if (next.phase !== "transcribing") {
      recorder.cancel();
      return;
    }
    const controller = new AbortController();
    transcribeRef.current = controller;
    try {
      const audio = await recorder.stop();
      const result = await rfqApi.transcribe(audio.blob, controller.signal);
      if (controller.signal.aborted) return;
      transcribeRef.current = null;
      const text = result.data.text.trim();
      if (!text) return void send({ type: "failed", kind: "no_speech" });
      send({ type: "transcribed" });
      transcriptRef.current(text);
    } catch (e) {
      if (controller.signal.aborted) return;
      transcribeRef.current = null;
      fail(e, "transcribe");
    }
  }, [fail, send]);

  const start = useCallback(async () => {
    const limits = configRef.current;
    if (!limits) return;
    stopSpeaking();
    if (send({ type: "mic_requested" }).phase !== "asking_permission") return;
    const recorder = createRecorder({
      maxSeconds: limits.max_seconds,
      onLimit: () => void finish(),
      // The browser ended the recording by itself: the recorder has released the microphone, the screen leaves
      // "recording". A recording that is already being sent is not ours to touch.
      onInterrupted: () => {
        if (recorderRef.current !== recorder) return;
        recorderRef.current = null;
        send({ type: "failed", kind: "failed" });
      },
    });
    recorderRef.current = recorder;
    try {
      await recorder.start();
    } catch (e) {
      if (recorderRef.current === recorder) recorderRef.current = null;
      recorder.cancel();
      fail(e, "record");
      return;
    }
    // The user may have cancelled while the browser asked for permission.
    if (recorderRef.current !== recorder || stateRef.current.phase !== "asking_permission") {
      recorder.cancel();
      return;
    }
    send({ type: "recording_started" });
  }, [fail, finish, send, stopSpeaking]);

  const cancel = useCallback(() => {
    const { phase } = stateRef.current;
    if (phase === "speaking") return stopSpeaking();
    if (!isCapturing(stateRef.current)) return;
    recorderRef.current?.cancel();
    recorderRef.current = null;
    transcribeRef.current?.abort();
    transcribeRef.current = null;
    send({ type: "cancelled" });
  }, [send, stopSpeaking]);

  const toggleMic = useCallback(() => {
    const { phase } = stateRef.current;
    if (phase === "recording") void finish();
    else if (phase === "asking_permission") cancel();
    else if (phase !== "transcribing") void start();
  }, [cancel, finish, start]);

  const speakAnswer = useCallback(
    (response: ChatResponse) => {
      const limits = configRef.current;
      if (!limits?.speak || !readAloudRef.current) return;
      const text = speakable(response, limits.max_speech_chars);
      if (!text || (stateRef.current.phase !== "idle" && stateRef.current.phase !== "error")) return;
      const controller = new AbortController();
      speechRef.current = controller;
      send({ type: "speech_started" });
      void (async () => {
        try {
          const bytes = await rfqApi.speak(text, controller.signal);
          if (controller.signal.aborted) return;
          await player().play(bytes);
          // Stopping aborts the controller, so a stopped sound does not end a later reading.
          if (controller.signal.aborted) return;
          speechRef.current = null;
          send({ type: "speech_ended" });
        } catch (e) {
          if (controller.signal.aborted) return;
          speechRef.current = null;
          fail(e, "speak");
        }
      })();
    },
    [fail, player, send],
  );

  const setReadAloud = useCallback(
    (on: boolean) => {
      readAloudRef.current = on;
      setReadAloudState(on);
      writeReadAloud(on);
      if (!on) stopSpeaking();
    },
    [stopSpeaking],
  );

  const { phase } = state;
  // The level and the clock come from the real recorder and player, sampled while one of them is running.
  useEffect(() => {
    if (phase !== "recording" && phase !== "speaking") return;
    const timer = setInterval(() => {
      const source = phase === "recording" ? recorderRef.current : playerRef.current;
      const raw = source ? source.level() : 0;
      level.set(Math.round(raw * LEVEL_PRECISION) / LEVEL_PRECISION);
      if (phase === "recording") setRecordedSeconds(Math.floor((recorderRef.current?.elapsedMs() ?? 0) / MS_PER_SECOND));
    }, LEVEL_STEP_MS);
    return () => {
      clearInterval(timer);
      level.set(0);
      setRecordedSeconds(0);
    };
  }, [phase, level]);

  // Escape drops the recording, or stops the reading.
  useEffect(() => {
    if (phase === "idle" || phase === "error") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [phase, cancel]);

  // Leaving the screen, or another account: nothing keeps recording or playing.
  useEffect(
    () => () => {
      recorderRef.current?.cancel();
      transcribeRef.current?.abort();
      speechRef.current?.abort();
      playerRef.current?.dispose();
    },
    [],
  );

  return {
    canListen: Boolean(config?.transcribe),
    canSpeak: Boolean(config?.speak),
    state,
    level,
    recordedSeconds,
    errorMessage: state.error ? voiceMessage(state.error) : null,
    readAloud,
    setReadAloud,
    toggleMic,
    cancel,
    speakAnswer,
    stopSpeaking,
  };
}

