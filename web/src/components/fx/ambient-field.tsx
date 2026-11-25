"use client";
/**
 * The background dot field: one fixed, click-through canvas behind the whole application (docs/design/immersive-ui.md §4.6).
 * Mounted once in layout.tsx. The field exists only while effects are on; reduced motion gives one still picture.
 * Strict Mode's mount, unmount, mount cycle leaves nothing behind: `destroy` removes the loop and every listener.
 */
import { useEffect, useRef } from "react";
import { AmbientField } from "@/lib/fx/ambient-layer";
import { useFxState } from "./use-fx";

export function AmbientFieldCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { enabled, reduced } = useFxState();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !enabled) return;
    let field: AmbientField;
    try {
      field = new AmbientField(canvas, { still: reduced });
    } catch {
      return; // No 2D canvas: the page simply has no background dots.
    }
    return () => field.destroy();
  }, [enabled, reduced]);

  return <canvas ref={canvasRef} className="fx-ambient" aria-hidden="true" data-fx-ambient="" />;
}
