"use client";
/**
 * What is on screen while the intro plays: the page-colour cover, two click-through canvases (the sphere, then the
 * dots) and a visible "건너뛰기" button. The choreography is `runIntro`; this only gives it its elements.
 *
 * The cover and canvases sit above the shared effects layer (z-fx-layer) on purpose: a screen header that reveals
 * itself while the intro plays does so out of sight and is already in place when the cover lifts.
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useFx } from "../fx/fx-provider";
import { Button } from "../ui/primitives";
import { APP_ROOT_ID, holdInert } from "./intro-inert";
import { TAGLINE, WORDMARK } from "./intro-plan";
import { runIntro, type IntroEnd, type IntroRun } from "./intro-player";

const above = (step: number): CSSProperties => ({ zIndex: `calc(var(--z-fx-layer) + ${step})` });
const CANVAS_CLASS = "pointer-events-none fixed inset-0 h-full w-full";

export function IntroStage({ onEnd }: { onEnd: (reason: IntroEnd) => void }) {
  const fx = useFx();
  const coverRef = useRef<HTMLDivElement>(null);
  const sphereRef = useRef<HTMLCanvasElement>(null);
  const layerRef = useRef<HTMLCanvasElement>(null);
  const skipRef = useRef<HTMLButtonElement>(null);
  const runRef = useRef<IntroRun | null>(null);
  const [canSkip, setCanSkip] = useState(true);

  useEffect(() => {
    const cover = coverRef.current;
    const sphereCanvas = sphereRef.current;
    const layerCanvas = layerRef.current;
    if (!cover || !sphereCanvas || !layerCanvas) return;
    // Focus that the skip button took (Tab) goes back to where it was, so the keyboard does not start over.
    // The screen underneath cannot take focus while the cover is up; every ending path releases it.
    const releaseInert = holdInert(document.getElementById(APP_ROOT_ID));
    const before = document.activeElement;
    const giveFocusBack = () => {
      if (skipRef.current && document.activeElement === skipRef.current && before instanceof HTMLElement && before !== document.body) {
        before.focus();
      }
    };
    const run = runIntro({
      sphereCanvas,
      layerCanvas,
      cover,
      onHandoff: () => {
        giveFocusBack();
        setCanSkip(false);
      },
      remember: (points) => fx?.remember(points),
      onEnd: (reason) => {
        releaseInert();
        giveFocusBack();
        onEnd(reason);
      },
    });
    runRef.current = run;
    return () => {
      run.cancel();
      releaseInert();
      runRef.current = null;
    };
  }, [fx, onEnd]);

  const skip = () => runRef.current?.skip();

  return (
    <>
      <div ref={coverRef} role="presentation" onClick={skip} className="fx-cover" style={above(0)} data-testid="intro-cover" />
      <p className="sr-only">
        {WORDMARK}. {TAGLINE}
      </p>
      <canvas ref={sphereRef} aria-hidden="true" className={CANVAS_CLASS} style={above(1)} />
      <canvas ref={layerRef} aria-hidden="true" className={CANVAS_CLASS} style={above(2)} />
      {canSkip && (
        <Button
          ref={skipRef}
          variant="ghost"
          onClick={skip}
          aria-keyshortcuts="Escape"
          className="fixed bottom-6 right-6"
          style={above(3)}
          data-testid="intro-skip"
        >
          건너뛰기
        </Button>
      )}
    </>
  );
}
