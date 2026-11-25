"use client";
/**
 * The pieces of the 3D flow map. Everything is flat: basic materials, no lights, no shadows, no fog.
 * One instanced mesh holds every clause box, two hold the file and department sheets, one line-segments
 * object holds the edges and a second one the chosen edges in the accent colour. Loaded only with the
 * scene chunk (this is the one place three.js comes in).
 */
import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, type ElementRef } from "react";
import { Color, Object3D, Vector3, type BufferGeometry, type InstancedMesh, type Points } from "three";

import { fitCamera } from "@/lib/topology/camera";
import { dotPositions, isFlowDone, MAX_FLOW_DOTS } from "@/lib/topology/flow";
import { BOX, type Bounds, type Layout, type Rect } from "@/lib/topology/layout";
import type { ClauseTone, SheetTone } from "@/lib/topology/selection";
import type { Palette } from "./palette";

export const FOV = 35;
const BOX_DEPTH = 0.3;
const SHEET_DEPTH = 0.5;
const ACCENT_EDGE_Z = 0.2;
const DOT_Z = 0.4;
const DOT_PX = 3.5;
const EDGE_OPACITY = 0.38;
const ORBIT = { azimuth: 1.0, polarMin: 0.95, polarMax: 2.2, speed: 0.6 };

const dummy = new Object3D();

function toneColor(palette: Palette, tone: ClauseTone | SheetTone): string {
  switch (tone) {
    case "danger":
      return palette.danger;
    case "accent":
      return palette.accent;
    case "hidden":
      return palette.line;
    case "muted":
      return palette.muted;
    default:
      return palette.ink;
  }
}

/** The pointer position inside the canvas, for the hover label. */
export function pointerOf(e: ThreeEvent<PointerEvent>): { x: number; y: number } {
  const native = e.nativeEvent;
  return { x: native.offsetX, y: native.offsetY };
}

// ── Clause boxes ────────────────────────────────────────────────────────────
interface BoxesProps {
  layout: Layout;
  tones: ClauseTone[];
  hovered: number | null;
  palette: Palette;
  onHover: (index: number | null, at: { x: number; y: number }) => void;
  onPick: (index: number) => void;
}

export function ClauseBoxes({ layout, tones, hovered, palette, onHover, onPick }: BoxesProps) {
  const ref = useRef<InstancedMesh>(null);
  const invalidate = useThree((s) => s.invalidate);
  const count = tones.length;

  // Matrix and colour of every box in one pass.
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const color = new Color();
    for (let i = 0; i < count; i += 1) {
      dummy.position.set(layout.clauseXY[i * 2], layout.clauseXY[i * 2 + 1], 0);
      dummy.scale.set(BOX, BOX, BOX_DEPTH);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, color.set(toneColor(palette, i === hovered && tones[i] !== "hidden" ? "accent" : tones[i])));
    }
    mesh.instanceMatrix.needsUpdate = true;
    // Picking tests against this sphere; it must follow the new matrices.
    mesh.computeBoundingSphere();
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    invalidate();
  }, [layout, tones, hovered, palette, count, invalidate]);

  if (count === 0) return null;
  return (
    <instancedMesh
      key={count}
      ref={ref}
      args={[undefined, undefined, count]}
      frustumCulled={false}
      onPointerMove={(e) => {
        e.stopPropagation();
        onHover(e.instanceId ?? null, pointerOf(e));
      }}
      onPointerOut={() => onHover(null, { x: 0, y: 0 })}
      onClick={(e) => {
        e.stopPropagation();
        if (e.instanceId !== undefined) onPick(e.instanceId);
      }}
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

// ── Sheets (files, departments) ─────────────────────────────────────────────
interface SheetsProps {
  rects: Rect[];
  colors: string[];
  onPick?: (index: number) => void;
}

export function Sheets({ rects, colors, onPick }: SheetsProps) {
  const ref = useRef<InstancedMesh>(null);
  const invalidate = useThree((s) => s.invalidate);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const color = new Color();
    rects.forEach((r, i) => {
      dummy.position.set(r.x, r.y, 0);
      dummy.scale.set(r.w, r.h, SHEET_DEPTH);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, color.set(colors[i]));
    });
    mesh.instanceMatrix.needsUpdate = true;
    // Picking tests against this sphere; it must follow the new matrices.
    mesh.computeBoundingSphere();
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    invalidate();
  }, [rects, colors, invalidate]);

  if (rects.length === 0) return null;
  return (
    <instancedMesh
      key={rects.length}
      ref={ref}
      args={[undefined, undefined, rects.length]}
      frustumCulled={false}
      onClick={
        onPick
          ? (e) => {
              e.stopPropagation();
              if (e.instanceId !== undefined) onPick(e.instanceId);
            }
          : undefined
      }
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

// ── Edges ───────────────────────────────────────────────────────────────────
/** x1, y1, x2, y2 per edge to x, y, z per end. */
function toPositions(segments: Float32Array, z: number): Float32Array {
  const n = segments.length / 4;
  const out = new Float32Array(n * 6);
  for (let i = 0; i < n; i += 1) {
    out.set([segments[i * 4], segments[i * 4 + 1], z, segments[i * 4 + 2], segments[i * 4 + 3], z], i * 6);
  }
  return out;
}

interface EdgesProps {
  segments: Float32Array;
  color: string;
  opacity: number;
  z?: number;
  order?: number;
}

export function Edges({ segments, color, opacity, z = 0, order = 0 }: EdgesProps) {
  const positions = useMemo(() => toPositions(segments, z), [segments, z]);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => invalidate(), [positions, color, invalidate]);
  if (positions.length === 0) return null;
  return (
    <lineSegments key={positions.length} renderOrder={order} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <lineBasicMaterial color={color} transparent={opacity < 1} opacity={opacity} toneMapped={false} />
    </lineSegments>
  );
}

export const BASE_EDGE_OPACITY = EDGE_OPACITY;
export const ACCENT_Z = ACCENT_EDGE_Z;

// ── Flow dots ───────────────────────────────────────────────────────────────
export interface FlowRequest {
  ids: number[];
}

interface FlowProps {
  edges: Float32Array;
  request: FlowRequest | null;
  enabled: boolean;
  color: string;
}

/** Dots travel once along the given edges, then the object hides itself. Nothing runs between flows. */
export function FlowDots({ edges, request, enabled, color }: FlowProps) {
  const points = useRef<Points>(null);
  const startedAt = useRef<number | null>(null);
  const positions = useMemo(() => new Float32Array(MAX_FLOW_DOTS * 3), []);
  const xy = useMemo(() => new Float32Array(MAX_FLOW_DOTS * 2), []);
  const { invalidate, gl } = useThree();

  useEffect(() => {
    const object = points.current;
    if (!object) return;
    if (!enabled || !request || request.ids.length === 0) {
      startedAt.current = null;
      object.visible = false;
    } else {
      startedAt.current = performance.now();
    }
    invalidate();
  }, [request, enabled, invalidate]);

  useFrame(() => {
    const object = points.current;
    if (!object || startedAt.current === null || !request) return;
    const elapsed = performance.now() - startedAt.current;
    if (isFlowDone(elapsed)) {
      startedAt.current = null;
      object.visible = false;
      return;
    }
    const shown = dotPositions(edges, request.ids, elapsed, xy);
    for (let i = 0; i < shown; i += 1) positions.set([xy[i * 2], xy[i * 2 + 1], DOT_Z], i * 3);
    const geometry = object.geometry as BufferGeometry;
    geometry.setDrawRange(0, shown);
    geometry.attributes.position.needsUpdate = true;
    object.visible = shown > 0;
    invalidate();
  });

  return (
    <points ref={points} visible={false} frustumCulled={false} renderOrder={3}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial color={color} size={DOT_PX * gl.getPixelRatio()} sizeAttenuation={false} toneMapped={false} />
    </points>
  );
}

// ── Camera and labels ───────────────────────────────────────────────────────
interface RigProps {
  bounds: Bounds;
  onReady: (reset: () => void) => void;
}

/** Puts the camera where the whole map fits, and hands the page a way back to that view. */
export function CameraRig({ bounds, onReady }: RigProps) {
  const controls = useRef<ElementRef<typeof OrbitControls>>(null);
  const { get, size, invalidate } = useThree();

  useLayoutEffect(() => {
    const c = controls.current;
    if (!c) return;
    const { camera } = get();
    const fit = fitCamera({ bounds, widthPx: size.width, heightPx: size.height, fovDeg: FOV });
    camera.position.set(fit.target[0], fit.target[1], fit.distance);
    camera.near = fit.distance * 0.05;
    camera.far = fit.distance * 6;
    camera.updateProjectionMatrix();
    c.target.set(fit.target[0], fit.target[1], 0);
    c.update();
    c.saveState();
    onReady(() => {
      c.reset();
      invalidate();
    });
    invalidate();
  }, [bounds, size.width, size.height, get, invalidate, onReady]);

  return (
    <OrbitControls
      ref={controls}
      enableZoom={false}
      enablePan={false}
      enableDamping={false}
      rotateSpeed={ORBIT.speed}
      minAzimuthAngle={-ORBIT.azimuth}
      maxAzimuthAngle={ORBIT.azimuth}
      minPolarAngle={ORBIT.polarMin}
      maxPolarAngle={ORBIT.polarMax}
    />
  );
}

export interface LabelAnchor {
  id: string;
  x: number;
  y: number;
  /** Which side of the point the text sits on. */
  side: "left" | "right";
}

interface ProjectorProps {
  anchors: LabelAnchor[];
  registry: Map<string, HTMLElement>;
}

/** Moves the DOM labels to where their anchors are on screen, after every rendered frame's camera update. */
export function LabelProjector({ anchors, registry }: ProjectorProps) {
  const point = useMemo(() => new Vector3(), []);
  useFrame(({ camera, size }) => {
    camera.updateMatrixWorld();
    for (const a of anchors) {
      const el = registry.get(a.id);
      if (!el) continue;
      point.set(a.x, a.y, 0).project(camera);
      const px = (point.x * 0.5 + 0.5) * size.width;
      const py = (-point.y * 0.5 + 0.5) * size.height;
      el.style.transform = `translate(${px.toFixed(1)}px, ${py.toFixed(1)}px) translate(${a.side === "left" ? "-100%" : "0"}, -50%)`;
      el.style.visibility = "visible";
    }
  });
  return null;
}

export { toneColor };
