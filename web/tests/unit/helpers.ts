/** Shared builders for the unit tests. All values are invented. */
import type { Viewer } from "@/lib/rfq/contract";

export const API = "http://api.test";

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

export const apiError = (status: number, code: string, message = "거절") => json({ error: { code, message } }, status);

export function viewer(patch: Partial<Viewer> = {}): Viewer {
  return {
    user_id: "u-1",
    username: "quality",
    display_name: "품질 담당",
    role: "member",
    department: "quality",
    department_label: "품질",
    group: "cost_restricted",
    full_scope: false,
    can_upload: false,
    can_manage: false,
    must_change_password: false,
    ...patch,
  };
}
