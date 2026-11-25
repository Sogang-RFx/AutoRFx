"use client";
/**
 * Dots on the eight-step timeline's own connector (docs/design/immersive-ui.md §4.3): they stream from the stage in progress to
 * the next, rest on finished stages, stop in the danger tone on a failed one and pass skipped ones. The only input is
 * the stage statuses of the job response. Draws nothing when effects are off (the page then looks as it always did).
 *
 * Put it inside a `relative` wrapper, before the `<ol>` so the icons paint over the dots; it measures the
 * `[data-stage-icon]` elements from there. Draws no dot until a stage has been seen running.
 */
import { useEffect, useRef, useState } from "react";
import { useFxMoves } from "@/components/fx/use-fx";
import type { FlowHandle } from "@/lib/fx/particles";
import { ParticleLayer } from "@/lib/fx/particles";
import type { StageStatus } from "@/lib/rfq/contract";
import { hasSeenRunning, railYs, stageLanes } from "./stage-gates";

/** Width of the canvas laid over the left edge of the rows; the connector x is measured from the icons. */
export const RAIL_WIDTH_PX = 60;
const MAX_DOTS = 400;
const DOT_COUNT = 140;
const FLOW_SPEED = 0.9;
const FLOW_SPREAD_PX = 3;
const REST_RADIUS_PX = 3;

export function StageFlow({ statuses }: { statuses: readonly StageStatus[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const flowRef = useRef<FlowHandle | null>(null);
  const measureRef = useRef<() => ReturnType<typeof stageLanes>>(() => []);
  const statusesRef = useRef(statuses);
  const moves = useFxMoves();
  // No dot until this page has seen a stage running: a finished job opens as the plain timeline.
  const [seen, setSeen] = useState(() => hasSeenRunning(false, statuses));

  // Adjusting state while rendering is the supported way to derive it from props.
  const nowSeen = hasSeenRunning(seen, statuses);
  if (nowSeen !== seen) setSeen(nowSeen);

  // New statuses from a poll: the dots move on from where they are.
  useEffect(() => {
    statusesRef.current = statuses;
    flowRef.current?.update(measureRef.current());
  }, [statuses]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = canvas?.parentElement;
    if (!moves || !seen || !canvas || !wrapper) return;
    let layer: ParticleLayer;
    try {
      layer = new ParticleLayer(canvas, { max: MAX_DOTS });
    } catch {
      return; // No 2D canvas: the timeline is complete without the dots.
    }
    const measure = () => {
      const icons = [...wrapper.querySelectorAll("[data-stage-icon]")].map((el) => el.getBoundingClientRect());
      if (!icons.length) return [];
      const box = canvas.getBoundingClientRect();
      const x = icons[0].left + icons[0].width / 2 - box.left;
      return stageLanes(statusesRef.current, x, railYs(icons.map((r) => r.bottom), box.top));
    };
    measureRef.current = measure;
    const flow = layer.flow({
      count: DOT_COUNT,
      lanes: measure(),
      speed: FLOW_SPEED,
      spread: FLOW_SPREAD_PX,
      restRadius: REST_RADIUS_PX,
    });
    flowRef.current = flow;
    // A row grows when its detail line arrives: the gates follow.
    const observer = new ResizeObserver(() => flow.update(measure()));
    observer.observe(wrapper);
    return () => {
      observer.disconnect();
      flowRef.current = null;
      measureRef.current = () => [];
      layer.destroy();
    };
  }, [moves, seen]);

  if (!moves || !seen) return null;
  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none absolute left-0 top-0 h-full"
      style={{ width: RAIL_WIDTH_PX }}
    />
  );
}
