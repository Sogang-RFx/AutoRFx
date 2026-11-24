"use client";
/**
 * App shell (TASKS S009, SPEC §14): top bar with RFQ package name, team switch,
 * engine indicator and presentation mode; 56px icon navigation rail.
 * Provides the package's file list to every screen.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import type { Department, Engines, Team } from "@/lib/rfq/contract";
import { departmentFilters, type DeptFilter } from "@/lib/rfq/departments";
import { PackageProvider } from "@/lib/rfq/package-context";
import { rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { untilReady } from "@/lib/rfq/until-ready";
import { useResource } from "@/lib/rfq/use-resource";
import {
  IconCalc,
  IconChat,
  IconCompare,
  IconDoc,
  IconGauge,
  IconGear,
  IconPresent,
  IconTable,
  IconTimeline,
} from "../ui/icons";
import { EngineBadge, cx } from "../ui/primitives";

type NavItem = { href: string; label: string; icon: ReactNode; costOnly?: boolean };

/** Current team and whether it is in the cost-restricted group (RBAC-03: unknown → restricted). */
export function useTeamGroup(): {
  team: Team | null;
  restricted: boolean;
  teams: Team[];
  departments: Department[];
  filters: DeptFilter[];
  engines: Engines | null;
  ready: boolean;
  error: string | null;
} {
  const session = useSession();
  const meta = useResource(session.ready ? "meta" : null, (s) => rfqApi.meta(s));
  const data = meta.status === "ok" ? meta.data.data : null;
  const teams = data?.teams ?? [];
  const departments = data?.departments ?? [];
  const found = teams.find((t) => t.id === session.team) ?? null;
  // Fails closed: restricted until team info proves otherwise.
  return {
    team: found,
    restricted: found?.group !== "cost_visible",
    teams,
    departments,
    filters: departmentFilters(teams, departments),
    engines: data?.engines ?? null,
    ready: meta.status === "ok",
    error: meta.status === "error" ? meta.error : null,
  };
}

export function AppShell({ rfqId, children }: { rfqId: string; children: ReactNode }) {
  const pathname = usePathname();
  const session = useSession();
  const { restricted, teams, engines } = useTeamGroup();
  // Right after upload the analysis is still running and the backend answers 409 not_ready;
  // the shell stays mounted across screens, so it must wait rather than keep that error.
  const doc = useResource(session.ready ? `doc:${rfqId}` : null, (s) =>
    untilReady(() => rfqApi.document(rfqId, s), s),
  );
  const info = doc.status === "ok" ? doc.data.data : null;

  const base = `/rfq/${encodeURIComponent(rfqId)}`;
  const nav: NavItem[] = [
    { href: `${base}/processing`, label: "처리", icon: <IconTimeline size={20} /> },
    { href: `${base}/clauses`, label: "조항", icon: <IconDoc size={20} /> },
    { href: `${base}/rtm`, label: "RTM", icon: <IconTable size={20} /> },
    { href: `${base}/consistency`, label: "정합성", icon: <IconCompare size={20} /> },
    { href: `${base}/precost`, label: "사전원가", icon: <IconCalc size={20} />, costOnly: true },
    { href: `${base}/chat`, label: "챗봇", icon: <IconChat size={20} /> },
    { href: `${base}/evaluation`, label: "평가", icon: <IconGauge size={20} /> },
  ];
  const visibleNav = nav.filter((n) => !(n.costOnly && restricted));
  const docName = info ? info.name : doc.status === "error" ? "문서 정보를 불러오지 못함" : "…";

  return (
    <PackageProvider files={info?.files ?? []}>
      <div className="flex min-h-dvh flex-col lg:h-dvh">
        <a
          href="#main"
          className="absolute left-2 top-2 z-50 -translate-y-20 rounded-control bg-ink px-3 py-2 text-white focus:translate-y-0"
        >
          본문으로 건너뛰기
        </a>
        <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-2">
          <Link href="/" className="font-serif text-heading font-semibold text-ink">
            AutoRFx
          </Link>
          <div className="min-w-0 flex-1 basis-48">
            <p className="truncate text-dense font-medium text-ink" title={docName}>
              {docName}
            </p>
            {info && (
              <p className="truncate text-label text-ink-muted tabular" data-testid="package-summary">
                파일 {info.files.length}개 · {info.page_count}페이지 · 패키지 SHA-256 {info.sha256.slice(0, 12)}…
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {engines && <EngineBadge engines={engines} />}
            <label className="flex items-center gap-1.5 text-label text-ink-muted">
              <span>팀</span>
              <select
                data-testid="team-select"
                value={session.team ?? ""}
                onChange={(e) => session.setTeam(e.target.value || null)}
                className="min-h-8 max-w-44 rounded-control border border-line-control bg-surface px-2 text-dense text-ink"
              >
                <option value="">선택 안 함 (금액 제한)</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                    {t.group === "cost_visible" ? " · 금액 열람" : ""}
                  </option>
                ))}
              </select>
            </label>
            <Link
              href="/settings/departments"
              data-testid="org-settings-link"
              className="inline-flex min-h-8 items-center rounded-control border border-line-control px-2 text-label font-medium text-ink transition-soft hover:bg-canvas"
            >
              부서 설정
            </Link>
            {!restricted && (
              <Link
                href="/settings/company"
                data-testid="company-settings-link"
                aria-label="사전원가 기본 설정"
                title="사전원가 기본 설정 (단가표 · 회사 프로필 · 비율)"
                className="inline-flex min-h-8 items-center gap-1 rounded-control border border-line-control px-2 text-label font-medium text-ink transition-soft hover:bg-canvas"
              >
                <IconGear size={14} />
                기본 설정
              </Link>
            )}
            <button
              type="button"
              aria-pressed={session.present}
              onClick={() => session.setPresent(!session.present)}
              className={cx(
                "inline-flex min-h-8 items-center gap-1.5 rounded-control border px-2 text-label font-medium transition-soft",
                session.present ? "border-ink bg-ink text-white" : "border-line-control text-ink hover:bg-canvas",
              )}
            >
              <IconPresent size={14} />
              발표 모드
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav
            aria-label="화면"
            className="flex shrink-0 overflow-x-auto border-b border-line bg-surface md:w-14 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:py-2"
          >
            {visibleNav.map((item) => {
              const active = pathname?.startsWith(item.href) ?? false;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "flex min-w-12 flex-1 flex-col items-center gap-0.5 px-0.5 py-1.5 text-label transition-soft md:min-w-0 md:flex-none md:py-2",
                    active ? "text-accent" : "text-ink-muted hover:text-ink",
                  )}
                >
                  <span
                    className={cx(
                      "flex size-8 items-center justify-center rounded-control transition-soft",
                      active && "bg-accent-tint",
                    )}
                  >
                    {item.icon}
                  </span>
                  <span className="whitespace-nowrap">{item.label}</span>
                </Link>
              );
            })}
          </nav>
          <main id="main" className="min-w-0 flex-1 lg:min-h-0 lg:overflow-y-auto">
            {children}
          </main>
        </div>
      </div>
    </PackageProvider>
  );
}
