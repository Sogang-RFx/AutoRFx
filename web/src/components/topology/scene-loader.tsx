"use client";
/**
 * The only door to three.js. The scene chunk is fetched here, after WebGL was found, and never on the
 * server. If the chunk or the scene fails, the error boundary reports it and the page shows the tables.
 */
import dynamic from "next/dynamic";
import { Component, useSyncExternalStore, type ReactNode } from "react";
import { Skeleton } from "@/components/ui/primitives";
import { hasWebGl } from "@/lib/topology/webgl";
import type { TopologySceneProps } from "./topology-scene";

const TopologyScene = dynamic(() => import("./topology-scene"), {
  ssr: false,
  loading: () => <Skeleton lines={10} />,
});

let cachedWebGl: boolean | null = null;
const readWebGl = (): boolean => (cachedWebGl ??= hasWebGl());
const noSubscription = (): (() => void) => () => undefined;

/** null while the server renders (and on the first client pass), then true or false. */
export function useWebGl(): boolean | null {
  return useSyncExternalStore(noSubscription, readWebGl, () => null);
}

interface BoundaryProps {
  onError: (reason: string) => void;
  children: ReactNode;
}

/** React has no function-component error boundary. */
class SceneBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    this.props.onError(error instanceof Error ? error.message : "장면을 그리지 못했습니다.");
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export function SceneLoader(props: TopologySceneProps) {
  return (
    <SceneBoundary onError={props.onFail}>
      <TopologyScene {...props} />
    </SceneBoundary>
  );
}
