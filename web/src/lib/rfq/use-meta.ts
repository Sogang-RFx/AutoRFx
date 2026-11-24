"use client";
import type { Department, Engines, MetaResponse } from "./contract";
import { rfqApi } from "./provider";
import { useResource } from "./use-resource";

export type Meta = {
  data: MetaResponse["data"] | null;
  departments: Department[];
  engines: Engines | null;
  /** Label of a department; the id itself while the list is not here. */
  departmentLabel: (id: string) => string;
  error: string | null;
};

/** Departments, engines and upload limits, the same for every account. */
export function useMeta(): Meta {
  const meta = useResource("meta", (s) => rfqApi.meta(s));
  const data = meta.status === "ok" ? meta.data.data : null;
  const departments = data?.departments ?? [];
  return {
    data,
    departments,
    engines: data?.engines ?? null,
    departmentLabel: (id) => departments.find((d) => d.id === id)?.label ?? id,
    error: meta.status === "error" ? meta.error : null,
  };
}
