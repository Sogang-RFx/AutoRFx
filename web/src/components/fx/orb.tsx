"use client";
/** A small sphere on its own canvas (chat empty state, voice). One still frame when effects are off. */
import { useEffect, useRef } from "react";
import { ParticleSphere, type SphereMode } from "@/lib/fx/sphere";
import { useFxMoves } from "./use-fx";

export interface OrbProps {
  mode: SphereMode;
  /** 0..1: microphone or playback level. Only real values; leave 0 when there is none. */
  level?: number;
  /** Width and height in CSS px. */
  size?: number;
  /** Each change ripples the sphere once (for example a stream event). Without changes a thinking orb is still. */
  pulseKey?: string | number;
  count?: number;
  className?: string;
}

const DEFAULT_SIZE = 96;

export function Orb({ mode, level = 0, size = DEFAULT_SIZE, count, pulseKey, className }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sphereRef = useRef<ParticleSphere | null>(null);
  const moves = useFxMoves();
  const lastPulse = useRef(pulseKey);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let sphere: ParticleSphere;
    try {
      sphere = new ParticleSphere(canvas, { count });
    } catch {
      return; // No 2D canvas: the space stays empty, nothing else depends on it.
    }
    sphereRef.current = sphere;
    if (!moves) sphere.drawStill();
    return () => {
      sphere.destroy();
      sphereRef.current = null;
    };
  }, [moves, count]);

  useEffect(() => {
    sphereRef.current?.setMode(mode);
  }, [mode, moves, count]);

  useEffect(() => {
    sphereRef.current?.setLevel(level);
  }, [level, moves, count]);

  useEffect(() => {
    if (Object.is(lastPulse.current, pulseKey)) return;
    lastPulse.current = pulseKey;
    if (moves) sphereRef.current?.pulse();
  }, [pulseKey, moves]);

  return <canvas ref={canvasRef} className={className} style={{ width: size, height: size }} aria-hidden="true" />;
}
