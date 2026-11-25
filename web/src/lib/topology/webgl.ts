/** Is WebGL there at all? Asked before the 3D chunk is downloaded, so a browser without it never fetches three.js. */
export function hasWebGl(doc: Pick<Document, "createElement"> = document): boolean {
  try {
    const canvas = doc.createElement("canvas");
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (!gl) return false;
    // Give the context back at once; the scene makes its own.
    (gl as WebGLRenderingContext).getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

/** A lost context is a failure only while the scene is alive; unmounting the canvas loses it on purpose. */
export function shouldReportContextLoss(state: { tornDown: boolean }): boolean {
  return !state.tornDown;
}
