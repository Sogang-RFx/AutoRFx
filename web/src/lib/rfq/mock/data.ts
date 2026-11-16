/**
 * Mock API business logic (SPEC §13.4). Reads the generated fixtures, applies
 * the team filter on the server (INV-8, SPEC §7) and returns contract-shaped
 * responses. Route handlers stay thin and call these functions.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { z } from "zod";
import {
  type ChatResponse,
  type ClauseItem,
  type ClausesResponse,
  type CostInputsResponse,
  type DocumentResponse,
  type EvaluationResponse,
  type Group,
  type JobResponse,
  type JobStage,
  type MetaResponse,
  type Part,
  type ProvenanceMap,
  type RtmResponse,
  type SentencesResponse,
  type UploadResponse,
  type VisibleClause,
  CostInputCandidate,
  Sentence,
  Team,
  VerifiedFinding,
  VisibleClause as VisibleClauseSchema,
} from "../contract";

const FIXTURE_DIR = path.join(process.cwd(), "src", "fixtures");

// ── Fixture schemas (contract pieces plus mock-only bookkeeping) ────────────
const DocumentFixture = z.object({
  id: z.string(),
  name: z.string(),
  file_name: z.string(),
  page_count: z.int(),
  sha256: z.string(),
  sha256_masked: z.string(),
});
const SentencesFixture = z.object({
  pages: z.array(z.object({ page_number: z.int(), sentences: z.array(Sentence) })),
});
const ClausesFixture = z.object({
  cost_sensitive: z.array(z.string()),
  scenario_unverified: z.array(z.string()),
  clauses: z.array(VisibleClauseSchema),
});
const ChatFixture = z.object({
  items: z.array(
    z.object({
      question: z.string(),
      keywords: z.array(z.string()),
      cost_sensitive: z.boolean(),
      answer: VerifiedFinding,
    }),
  ),
});
const CostInputsFixture = z.object({ item: z.string(), candidates: z.array(CostInputCandidate) });
const MetaFixture = z.object({
  assumption_note: z.string(),
  departments: z.array(z.object({ id: z.string(), label: z.string() })),
  teams: z.array(Team),
});

function load<T extends z.ZodType>(schema: T, file: string): z.infer<T> {
  const raw: unknown = JSON.parse(readFileSync(path.join(FIXTURE_DIR, "generated", file), "utf8"));
  return schema.parse(raw);
}

export const fixtures = {
  document: load(DocumentFixture, "document.json"),
  sentences: load(SentencesFixture, "sentences.json"),
  clauses: load(ClausesFixture, "clauses.json"),
  chat: load(ChatFixture, "chat.json"),
  costInputs: load(CostInputsFixture, "cost-inputs.json"),
  meta: load(MetaFixture, "meta.json"),
};

const COST_SENSITIVE = new Set(fixtures.clauses.cost_sensitive);

// ── Errors and common inputs ────────────────────────────────────────────────
export class MockError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export const SCENARIOS = ["default", "unverified", "empty", "error"] as const;
export type Scenario = (typeof SCENARIOS)[number];

export function parseScenario(value: string | null): Scenario {
  return (SCENARIOS as readonly string[]).includes(value ?? "") ? (value as Scenario) : "default";
}

/** RBAC-03: unknown or missing team falls into the restricted group. */
export function groupOf(team: string | null | undefined): Group {
  return fixtures.meta.teams.find((t) => t.id === team)?.group ?? "cost_restricted";
}

function provenance(parts: Part[]): ProvenanceMap {
  return Object.fromEntries(parts.map((p) => [p, "mock"])) as ProvenanceMap;
}

function assertDoc(id: string): void {
  if (id !== fixtures.document.id) throw new MockError(404, "rfq_not_found", `RFQ '${id}'를 찾을 수 없습니다.`);
}

// ── Pure policy functions (unit-tested) ──────────────────────────────────────
export function applyScenario(clauses: VisibleClause[], scenario: Scenario): VisibleClause[] {
  if (scenario === "empty") return [];
  if (scenario === "error") {
    throw new MockError(500, "scenario_error", "시연용 오류 시나리오: 분석 결과를 불러오지 못했습니다.");
  }
  if (scenario !== "unverified") return clauses;
  const targets = new Set(fixtures.clauses.scenario_unverified);
  return clauses.map((c) =>
    targets.has(c.section_id)
      ? {
          ...c,
          summary: {
            finding: { ...c.summary.finding, citations: [] },
            citation_status: "unverified",
            failed_citations: c.summary.finding.citations,
          },
        }
      : c,
  );
}

/** INV-8: restricted teams get only `{kind: "masked", section_id}` for cost clauses. */
export function maskForGroup(clauses: VisibleClause[], group: Group): ClauseItem[] {
  return clauses.map((c) =>
    group === "cost_restricted" && COST_SENSITIVE.has(c.section_id)
      ? { kind: "masked", section_id: c.section_id }
      : c,
  );
}

export function isCostSensitive(sectionId: string): boolean {
  return COST_SENSITIVE.has(sectionId);
}

// ── Handlers ─────────────────────────────────────────────────────────────────
const SAMPLE_NOTICE = "목업: 업로드한 파일은 전송하거나 저장하지 않습니다. 화면의 결과는 합성 샘플 RFQ의 것입니다.";

export function upload(fileName: string | null): UploadResponse {
  const notice = fileName ? `"${fileName}"은(는) 전송하지 않았습니다. ${SAMPLE_NOTICE}` : SAMPLE_NOTICE;
  return { data: { id: fixtures.document.id, started_at: Date.now(), notice }, provenance: provenance(["document"]) };
}

export function getDocument(id: string): DocumentResponse {
  assertDoc(id);
  const d = fixtures.document;
  return {
    data: { id: d.id, name: d.name, page_count: d.page_count, sha256: d.sha256, is_sample: true, notice: SAMPLE_NOTICE },
    provenance: provenance(["document"]),
  };
}

const STAGES: ReadonlyArray<Omit<JobStage, "status"> & { ms: number }> = [
  { id: "store", label: "원본 보관", detail: "원본 파일 해시를 기록하고 그대로 보관", part: "document", ms: 700 },
  { id: "canonical", label: "기준 PDF", detail: "인용 위치의 기준이 되는 PDF 확보", part: "source", ms: 900 },
  { id: "parse", label: "조항 파싱", detail: "조항 계층, 문단, 문장, 표 추출", part: "parsing", ms: 1400 },
  { id: "translate", label: "번역", detail: "조항별 국문 참고 번역", part: "translation", ms: 1200 },
  { id: "route", label: "부서 분류", detail: "조항을 담당 부서로 배정", part: "analysis", ms: 1000 },
  { id: "assess", label: "요건 강도 · 독소조항", detail: "Must/Should/Optional/Info와 독소조항 판정", part: "analysis", ms: 1300 },
  { id: "verify", label: "인용 검증", detail: "결과마다 원문 위치가 실제로 있는지 대조", part: "citation", ms: 900 },
];

export function getJob(id: string, since: number, speed: number, scenario: Scenario): JobResponse {
  assertDoc(id);
  const rate = Math.min(20, Math.max(0.25, Number.isFinite(speed) ? speed : 1));
  const elapsed = Math.max(0, (Date.now() - since) * rate);
  const failAt = scenario === "error" ? 2 : -1;
  let acc = 0;
  let halted = false;
  const stages: JobStage[] = STAGES.map(({ ms, ...stage }, index) => {
    const start = acc;
    acc += ms;
    if (halted || elapsed < start) return { ...stage, status: "pending" };
    if (index === failAt) {
      halted = true;
      return { ...stage, status: "error", detail: "시연용 오류 시나리오: 이 단계에서 처리가 멈췄습니다." };
    }
    return { ...stage, status: elapsed >= acc ? "done" : "running" };
  });
  const done = stages.every((s) => s.status === "done");
  const parts = [...new Set(STAGES.map((s) => s.part))];
  return { data: { started_at: since, done, stages }, provenance: provenance(parts) };
}

const ANALYSIS_PARTS: Part[] = ["parsing", "analysis", "citation", "translation"];

export function getClauses(
  id: string,
  team: string | null,
  department: string | null,
  scenario: Scenario,
): ClausesResponse {
  assertDoc(id);
  const group = groupOf(team);
  // Mask first, then filter: masked rows stay in every department view so the
  // filter cannot reveal which department a hidden clause belongs to (SPEC §7).
  const clauses = maskForGroup(applyScenario(fixtures.clauses.clauses, scenario), group).filter(
    (c) => c.kind === "masked" || !department || c.departments.includes(department),
  );
  return { data: { group, clauses }, provenance: provenance(ANALYSIS_PARTS) };
}

export function getSentences(id: string, team: string | null, page: number): SentencesResponse {
  assertDoc(id);
  const found = fixtures.sentences.pages.find((p) => p.page_number === page);
  if (!found) throw new MockError(404, "page_not_found", `${page}페이지가 없습니다.`);
  const restricted = groupOf(team) === "cost_restricted";
  const sentences = found.sentences.filter((s) => !(restricted && COST_SENSITIVE.has(s.locus.section_id)));
  return { data: { page_number: page, sentences }, provenance: provenance(["source"]) };
}

export function sourcePdf(id: string, team: string | null): Buffer {
  assertDoc(id);
  const file = groupOf(team) === "cost_restricted" ? "sample-rfq.masked.pdf" : "sample-rfq.pdf";
  return readFileSync(path.join(FIXTURE_DIR, "sample", file));
}

export function getRtm(id: string, team: string | null, scenario: Scenario): RtmResponse {
  assertDoc(id);
  const group = groupOf(team);
  const rows = maskForGroup(applyScenario(fixtures.clauses.clauses, scenario), group).map((c) =>
    c.kind === "masked"
      ? c
      : {
          kind: "clause" as const,
          section_id: c.section_id,
          title: c.title,
          departments: c.departments,
          priority: c.priority,
          red_flag: c.red_flag?.type ?? null,
          page_number: c.excerpt[0]?.locus.page_number ?? c.page_number,
          sentence_index: c.excerpt[0]?.locus.sentence_index ?? 1,
          citation_status: c.summary.citation_status,
        },
  );
  return { data: { group, rows }, provenance: provenance(ANALYSIS_PARTS) };
}

export function assertCostVisible(team: string | null): void {
  if (groupOf(team) === "cost_restricted") {
    throw new MockError(403, "cost_restricted", "이 팀에는 원가 정보가 제공되지 않습니다.");
  }
}

export function getCostInputs(id: string, team: string | null): CostInputsResponse {
  assertDoc(id);
  assertCostVisible(team);
  return { data: fixtures.costInputs, provenance: provenance(["cost_inputs"]) };
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, "");
}

export function chat(id: string, team: string | null, question: string): ChatResponse {
  assertDoc(id);
  const restricted = groupOf(team) === "cost_restricted";
  const q = normalize(question);
  const suggestions = fixtures.chat.items.filter((i) => !(restricted && i.cost_sensitive)).map((i) => i.question);
  const match = fixtures.chat.items.find(
    (i) => normalize(i.question) === q || i.keywords.some((k) => q.includes(normalize(k))),
  );
  const base = { provenance: provenance(["chat"]) };
  if (match && restricted && match.cost_sensitive) {
    return {
      ...base,
      data: {
        status: "restricted",
        answer: null,
        message: "이 질문의 근거는 금액 조항이라 이 팀에는 답을 제공하지 않습니다.",
        suggestions,
      },
    };
  }
  if (!match) {
    return {
      ...base,
      data: {
        status: "not_prepared",
        answer: null,
        message: "목업 챗봇은 준비된 질문에만 답하고, 답을 지어내지 않습니다. 아래 질문 중 하나를 골라 주세요.",
        suggestions,
      },
    };
  }
  return { ...base, data: { status: "answered", answer: match.answer, message: "준비된 문답입니다.", suggestions } };
}

export function getEvaluation(id: string): EvaluationResponse {
  assertDoc(id);
  return {
    data: {
      status: "not_scored",
      message: "아직 정답지로 채점하지 않았습니다. 도메인 팀의 정답지가 들어오면 지표가 표시됩니다.",
    },
    provenance: provenance(["evaluation"]),
  };
}

export function getMeta(): MetaResponse {
  return { data: fixtures.meta, provenance: {} };
}
