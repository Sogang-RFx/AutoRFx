"use client";
/**
 * The intro, mounted once at the root (docs/design/immersive-ui.md §4.1). It decides in a layout effect, before the
 * first paint, so the cover is up before the screen underneath can show. On the server and in the first hydration
 * pass it renders nothing, which is also what every visit without an intro gets.
 *
 * Strict Mode runs the decision twice; it only ever sets one flag, so that is harmless. The "seen" flag is written
 * when an intro ends, not when it starts, so a development remount does not swallow the first play.
 */
import { useCallback, useLayoutEffect, useState } from "react";
import { fxMoves, onIntroReplay } from "@/lib/fx/prefs";
import { markIntroSeen, readIntroSeen, shouldPlayIntro } from "./intro-plan";
import { IntroStage } from "./intro-stage";

const sessionStore = () => window.sessionStorage;

export function Intro() {
  const [isPlaying, setIsPlaying] = useState(false);

  useLayoutEffect(() => {
    const decide = (replay: boolean) => {
      const play = shouldPlayIntro({ search: window.location.search, seen: readIntroSeen(sessionStore), moves: fxMoves(), replay });
      if (play) setIsPlaying(true);
    };
    decide(false);
    return onIntroReplay(() => decide(true));
  }, []);

  const handleEnd = useCallback(() => {
    markIntroSeen(sessionStore);
    setIsPlaying(false);
  }, []);

  return isPlaying ? <IntroStage onEnd={handleEnd} /> : null;
}
