"use client";
/**
 * One fixed, full-screen, click-through canvas for every screen, and the controller that reveals
 * `[data-particle-target]` elements on it. Mounted once in layout.tsx.
 *
 * The controller is created during render (it touches nothing until it is used) and freed in an effect cleanup, so
 * Strict Mode's mount, unmount, mount cycle leaves it working: it creates its layer again on the next use.
 */
import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { loadGate } from "@/lib/fx/first-load";
import { createFxController, type FxController } from "@/lib/fx/reveal";

const FxContext = createContext<FxController | null>(null);

export function FxProvider({ children }: { children: ReactNode }) {
  const [controller] = useState(() => createFxController());
  // Children's layout effects run before this one, so they still see the first commit as "hydrating".
  useLayoutEffect(() => loadGate.endFirstCommit(), []);
  useEffect(() => () => controller.detach(), [controller]);
  return (
    <FxContext.Provider value={controller}>
      {children}
      <canvas ref={(el) => controller.setCanvas(el)} className="fx-layer" aria-hidden="true" data-fx-layer="" />
    </FxContext.Provider>
  );
}

/** The controller, or `null` outside a provider (then nothing is revealed and content is simply shown). */
export function useFx(): FxController | null {
  return useContext(FxContext);
}
