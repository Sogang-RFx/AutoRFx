"use client";
/**
 * The 3D flow map (docs/design/immersive-ui.md §4.5). Default export so `next/dynamic` can load it on its
 * own: this module and `scene-parts` are the only ones that import three.js.
 * `frameloop="demand"`: a frame is drawn only after an interaction and during the short flow animation.
 * File and department names are DOM text laid over the canvas, so they stay real text.
 */
import { Canvas } from "@react-three/fiber";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useFxMoves } from "@/components/fx/use-fx";
import { cx } from "@/components/ui/primitives";
import { sampleEdges } from "@/lib/topology/flow";
import type { Layout } from "@/lib/topology/layout";
import type { TopoModel } from "@/lib/topology/model";
import { shouldReportContextLoss } from "@/lib/topology/webgl";
import {
  accentEdges,
  clauseTones,
  departmentTones,
  pickSegments,
  type Resolved,
} from "@/lib/topology/selection";
import { LOCKED_CLAUSE } from "@/lib/rfq/scope";
import { readPalette } from "./palette";
import {
  ACCENT_Z,
  BASE_EDGE_OPACITY,
  CameraRig,
  ClauseBoxes,
  Edges,
  FOV,
  FlowDots,
  LabelProjector,
  Sheets,
  toneColor,
  type FlowRequest,
  type LabelAnchor,
} from "./scene-parts";

/** What opens the flow: nothing chosen yet means "all edges once"; a department means its own edges. */
export interface FlowTrigger {
  token: number;
  department: string | null;
}

export interface TopologySceneProps {
  model: TopoModel;
  layout: Layout;
  chosen: Resolved;
  flow: FlowTrigger;
  onPickClause: (index: number) => void;
  onPickDepartment: (index: number) => void;
  /** The scene cannot go on (context lost): the page shows the tables instead. */
  onFail: (reason: string) => void;
}

const LABEL_GAP = 0.45;
const LABEL_MAX_WIDTH = "calc(min(150px, 26%) - 10px)";
const ALL_EDGES_LIMIT = 4000;
const TIP_OFFSET = 14;

function flowRequest(model: TopoModel, layoutEdgesCount: number, flow: FlowTrigger): FlowRequest | null {
  if (flow.department !== null) {
    const index = model.departments.findIndex((d) => d.id === flow.department);
    const ids = model.edges.flatMap((e, i) => (e.department === index ? [i] : []));
    return index < 0 ? null : { ids: sampleEdges(ids) };
  }
  if (flow.token > 0) return null;
  return { ids: sampleEdges(Array.from({ length: Math.min(layoutEdgesCount, ALL_EDGES_LIMIT) }, (_, i) => i)) };
}

function anchorsOf(model: TopoModel, layout: Layout): LabelAnchor[] {
  const files = layout.files.map((r, i) => ({ id: `file:${model.files[i].id}`, x: r.x - r.w / 2 - LABEL_GAP, y: r.y, side: "left" as const }));
  const depts = layout.departments.map((r, i) => ({ id: `dept:${model.departments[i].id}`, x: r.x + r.w / 2 + LABEL_GAP, y: r.y, side: "right" as const }));
  return [...files, ...depts];
}

export default function TopologyScene(props: TopologySceneProps) {
  const { model, layout, chosen, flow, onPickClause, onPickDepartment, onFail } = props;
  const palette = useMemo(() => readPalette(), []);
  const moves = useFxMoves();
  const [hover, setHover] = useState<number | null>(null);
  const tip = useRef<HTMLDivElement>(null);
  const tipAt = useRef({ x: 0, y: 0 });
  const [registry] = useState(() => new Map<string, HTMLElement>());
  const resetView = useRef<() => void>(() => undefined);
  const onReady = useCallback((reset: () => void) => void (resetView.current = reset), []);

  // The canvas element, once the renderer exists. The loss listener lives in an effect so it is removed on
  // unmount; R3F's own teardown forces a context loss that must not count as a failure.
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null);
  const failRef = useRef(onFail);
  useEffect(() => {
    failRef.current = onFail;
  }, [onFail]);
  useEffect(() => {
    if (!canvas) return;
    const state = { tornDown: false };
    const onLost = () => {
      if (shouldReportContextLoss(state)) failRef.current("WebGL 그림판을 잃었습니다.");
    };
    canvas.addEventListener("webglcontextlost", onLost);
    return () => {
      state.tornDown = true;
      canvas.removeEventListener("webglcontextlost", onLost);
    };
  }, [canvas]);

  const tones = useMemo(() => clauseTones(model, chosen), [model, chosen]);
  const sheetTones = useMemo(() => departmentTones(model, chosen), [model, chosen]);
  const deptColors = useMemo(() => sheetTones.map((t) => toneColor(palette, t)), [sheetTones, palette]);
  const fileColors = useMemo(() => model.files.map(() => palette.muted), [model, palette]);
  const chosenSegments = useMemo(() => pickSegments(layout.edges, accentEdges(model, chosen)), [model, layout, chosen]);
  const request = useMemo(() => flowRequest(model, model.edges.length, flow), [model, flow]);
  const anchors = useMemo(() => anchorsOf(model, layout), [model, layout]);

  const placeTip = useCallback(() => {
    const el = tip.current;
    if (!el) return;
    // Keep the label inside the canvas, flipping to the left of the pointer near the right edge.
    const room = (el.parentElement?.clientWidth ?? Infinity) - el.offsetWidth - TIP_OFFSET;
    const x = tipAt.current.x + TIP_OFFSET > room ? tipAt.current.x - TIP_OFFSET - el.offsetWidth : tipAt.current.x + TIP_OFFSET;
    el.style.transform = `translate(${Math.max(0, x)}px, ${tipAt.current.y + TIP_OFFSET}px)`;
  }, []);
  useLayoutEffect(placeTip, [hover, placeTip]);

  const onHover = useCallback(
    (index: number | null, at: { x: number; y: number }) => {
      tipAt.current = at;
      setHover((prev) => (prev === index ? prev : index));
      placeTip();
    },
    [placeTip],
  );

  const hovered = hover === null ? null : model.clauses[hover];
  const hoverLabel = hovered ? (hovered.isVisible ? `${hovered.number} ${hovered.title}`.trim() : LOCKED_CLAUSE) : null;

  return (
    <div className={cx("relative h-full w-full", hovered?.isVisible && "cursor-pointer")}>
      <Canvas
        aria-hidden="true"
        frameloop="demand"
        dpr={[1, 2]}
        flat
        gl={{ antialias: true, alpha: false, powerPreference: "low-power" }}
        camera={{ fov: FOV, near: 0.1, far: 1000, position: [0, 0, 60] }}
        onCreated={({ gl }) => setCanvas(gl.domElement)}
      >
        <color attach="background" args={[palette.canvas]} />
        <CameraRig bounds={layout.bounds} onReady={onReady} />
        <Sheets rects={layout.files} colors={fileColors} />
        <Sheets rects={layout.departments} colors={deptColors} onPick={onPickDepartment} />
        <Edges segments={layout.edges} color={palette.lineStrong} opacity={BASE_EDGE_OPACITY} />
        <Edges segments={chosenSegments} color={palette.accent} opacity={1} z={ACCENT_Z} order={2} />
        <ClauseBoxes
          layout={layout}
          tones={tones}
          hovered={hover}
          palette={palette}
          onHover={onHover}
          onPick={onPickClause}
        />
        <FlowDots edges={layout.edges} request={request} enabled={moves} color={palette.accent} />
        <LabelProjector anchors={anchors} registry={registry} />
      </Canvas>

      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {model.files.map((f) => (
          <span
            key={f.id}
            ref={(el) => void (el ? registry.set(`file:${f.id}`, el) : registry.delete(`file:${f.id}`))}
            style={{ visibility: "hidden", maxWidth: LABEL_MAX_WIDTH }}
            className="absolute left-0 top-0 truncate text-label text-ink-muted"
            title={f.name}
          >
            <span className="font-semibold text-ink">{f.tag}</span> {f.name}
          </span>
        ))}
        {model.departments.map((d, i) => (
          <button
            key={d.id}
            type="button"
            ref={(el) => void (el ? registry.set(`dept:${d.id}`, el) : registry.delete(`dept:${d.id}`))}
            style={{ visibility: "hidden", maxWidth: LABEL_MAX_WIDTH }}
            aria-pressed={chosen?.kind === "department" && chosen.index === i}
            onClick={() => onPickDepartment(i)}
            className={cx(
              "pointer-events-auto absolute left-0 top-0 truncate rounded-chip text-left text-label transition-soft hover:text-accent",
              chosen?.kind === "department" && chosen.index === i ? "font-semibold text-accent" : "text-ink",
            )}
          >
            {d.label} <span className="tabular text-ink-muted">{d.lead}</span>
          </button>
        ))}
        {hoverLabel && (
          <div
            ref={tip}
            role="presentation"
            className="absolute left-0 top-0 max-w-64 rounded-control border border-line bg-surface px-2 py-1 text-label text-ink shadow-float"
          >
            {hoverLabel}
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={() => resetView.current()}
        className="absolute bottom-2 right-2 inline-flex min-h-8 items-center rounded-control border border-line-control bg-surface px-2 text-label font-medium text-ink transition-soft hover:bg-canvas"
      >
        처음 보기로
      </button>
    </div>
  );
}
