/**
 * The canvas side of the background dot field (docs/design/immersive-ui.md §4.6): owns the frame loop, the dot
 * arrays and the drawing. A frame is one clear and one small rectangle per dot, grouped by tone and opacity step so
 * the fill style changes only a few times. No React here; the component only starts and destroys it.
 */
import {
  ALPHA_LEVELS,
  ambientFrameMode,
  createDots,
  DOT_SIZES_PX,
  levelAlpha,
  resizeDots,
  stepDot,
  type AmbientDots,
} from "./ambient";
import { createCanvasHost, decayPointer, readToneColors, type CanvasHost } from "./canvas-kit";
import { seededRandom } from "./motion";
import { TONE } from "./tone";

export interface AmbientOptions {
  /** Reduced motion: draw the dots once and stop. */
  still?: boolean;
  /** Repeatable placement for tests. */
  random?: () => number;
}

const TONES_DRAWN = [TONE.ink, TONE.muted] as const;
const FLOW_CLOCK_START_S = 0;

export class AmbientField {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly host: CanvasHost;
  private readonly dots: AmbientDots = createDots();
  private readonly colors: string[];
  private readonly random: () => number;
  private readonly still: boolean;
  private clock = FLOW_CLOCK_START_S;
  /** Size key of the still picture on the canvas; resizing the canvas wipes it. */
  private stillKey = "";
  private isDestroyed = false;
  private framesDrawn = 0;

  constructor(private readonly canvas: HTMLCanvasElement, options: AmbientOptions = {}) {
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) throw new Error("2D canvas is not available");
    this.ctx = ctx;
    this.still = options.still ?? false;
    this.random = options.random ?? seededRandom(20261125);
    this.colors = readToneColors();
    this.host = createCanvasHost(canvas, (dt) => this.frame(dt), {
      // A still picture has no loop to wake: only a resize or a tab change redraws it.
      wakesOnPointer: () => !this.still,
    });
    this.host.wake();
  }

  /** Frames drawn so far (for the harness and tests). */
  get frames(): number {
    return this.framesDrawn;
  }

  get dotCount(): number {
    return this.dots.count;
  }

  /** Stops the loop, removes every listener and wipes the canvas. Safe to call twice. */
  destroy(): void {
    if (this.isDestroyed) return;
    this.isDestroyed = true;
    this.host.dispose();
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  private frame(dt: number): boolean {
    if (this.isDestroyed) return false;
    const { w, h, dpr } = this.host.size;
    resizeDots(this.dots, w, h, this.random);
    const key = `${w}x${h}@${dpr}`;
    const mode = ambientFrameMode({
      enabled: true,
      reduced: this.still,
      hidden: document.hidden,
      dots: this.dots.count,
      stillDrawn: key === this.stillKey,
    });
    if (mode === "none") return false;
    if (mode === "still") {
      this.draw(dpr);
      this.stillKey = key;
      return false;
    }
    this.advance(dt, w, h);
    this.draw(dpr);
    return true;
  }

  private advance(dt: number, w: number, h: number): void {
    this.clock += dt;
    decayPointer(this.host.pointer, dt);
    const d = this.dots;
    for (let i = 0; i < d.count; i += 1) stepDot(d, i, dt, this.clock, w, h, this.host.pointer);
  }

  private draw(dpr: number): void {
    const { ctx } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    for (const tone of TONES_DRAWN) {
      ctx.fillStyle = this.colors[tone];
      for (let level = 0; level < ALPHA_LEVELS; level += 1) {
        ctx.globalAlpha = levelAlpha(level);
        this.fillGroup(tone, level, dpr);
      }
    }
    ctx.globalAlpha = 1;
    this.framesDrawn += 1;
  }

  /** One rectangle per dot of this tone and opacity step, snapped to the device pixel grid so edges stay crisp. */
  private fillGroup(tone: number, level: number, dpr: number): void {
    const { ctx, dots: d } = this;
    for (let i = 0; i < d.count; i += 1) {
      if (d.tone[i] !== tone || d.level[i] !== level) continue;
      const edge = Math.max(1, Math.round(DOT_SIZES_PX[d.size[i]] * dpr));
      ctx.fillRect(Math.round((d.x[i] + d.ox[i]) * dpr), Math.round((d.y[i] + d.oy[i]) * dpr), edge, edge);
    }
  }
}
