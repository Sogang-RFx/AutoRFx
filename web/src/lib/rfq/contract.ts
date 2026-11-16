/**
 * AutoRFx response contract (SPEC §4, §13.3).
 *
 * Single source of truth: these zod schemas. `pnpm contract:emit` writes the
 * JSON Schema files in `/contract`, which the Python backend validates against
 * later (T005). The unit test fails when the committed JSON Schema drifts from
 * these definitions.
 */
import { z } from "zod";

// ── Parts and provenance ────────────────────────────────────────────────────
export const PARTS = [
  "document",
  "parsing",
  "analysis",
  "citation",
  "translation",
  "source",
  "cost_inputs",
  "precost",
  "chat",
  "evaluation",
] as const;
export const Part = z.enum(PARTS);
export type Part = z.infer<typeof Part>;

export const Provenance = z.enum(["mock", "real"]);
export type Provenance = z.infer<typeof Provenance>;

/** Which parts a response carries, and where each came from. */
export const ProvenanceMap = z.partialRecord(Part, Provenance);
export type ProvenanceMap = z.infer<typeof ProvenanceMap>;

// ── Location model ──────────────────────────────────────────────────────────
export const Locus = z.object({
  source_doc: z.string().min(1),
  page_number: z.int().min(1),
  section_id: z.string().min(1),
  paragraph_index: z.int().min(1),
  sentence_index: z.int().min(1),
});
export type Locus = z.infer<typeof Locus>;

/** Normalized rectangle, origin top-left, 0..1 of page width/height. */
export const Rect = z.object({
  x: z.number().min(0).max(1),
  y: z.number().min(0).max(1),
  w: z.number().min(0).max(1),
  h: z.number().min(0).max(1),
});
export type Rect = z.infer<typeof Rect>;

export const Sentence = z.object({
  locus: Locus,
  text: z.string().min(1),
  /** One rect per rendered line; a wrapped sentence has several. */
  rects: z.array(Rect),
});
export type Sentence = z.infer<typeof Sentence>;

// ── Analysis ────────────────────────────────────────────────────────────────
export const Priority = z.enum(["must", "should", "optional", "info"]);
export type Priority = z.infer<typeof Priority>;

export const RedFlagType = z.enum([
  "forced_cost_reduction",
  "unlimited_liquidated_damages",
  "ownership_transfer",
  "unilateral_termination",
]);
export type RedFlagType = z.infer<typeof RedFlagType>;

export const CitationStatus = z.enum(["verified", "unverified"]);
export type CitationStatus = z.infer<typeof CitationStatus>;

export const Finding = z.object({
  kind: z.enum(["summary", "priority", "red_flag", "answer", "cost_input"]),
  departments: z.array(z.string()),
  text: z.string(),
  priority: Priority.nullable(),
  red_flag_reason: z.string().nullable(),
  citations: z.array(Locus),
});
export type Finding = z.infer<typeof Finding>;

export const VerifiedFinding = z.object({
  finding: Finding,
  citation_status: CitationStatus,
  failed_citations: z.array(Locus),
});
export type VerifiedFinding = z.infer<typeof VerifiedFinding>;

export const SourceExcerpt = z.object({ locus: Locus, text: z.string() });
export type SourceExcerpt = z.infer<typeof SourceExcerpt>;

export const VisibleClause = z.object({
  kind: z.literal("clause"),
  section_id: z.string(),
  section_path: z.array(z.string()),
  title: z.string(),
  page_number: z.int().min(1),
  departments: z.array(z.string()),
  priority: Priority,
  red_flag: z.object({ type: RedFlagType, reason: z.string() }).nullable(),
  excerpt: z.array(SourceExcerpt),
  summary: VerifiedFinding,
  translation_ko: z.string().nullable(),
});
export type VisibleClause = z.infer<typeof VisibleClause>;

/** INV-8: a masked clause carries its number and the flag, nothing else. */
export const MaskedClause = z.strictObject({
  kind: z.literal("masked"),
  section_id: z.string(),
});
export type MaskedClause = z.infer<typeof MaskedClause>;

export const ClauseItem = z.discriminatedUnion("kind", [VisibleClause, MaskedClause]);
export type ClauseItem = z.infer<typeof ClauseItem>;

// ── Responses ───────────────────────────────────────────────────────────────
const envelope = <T extends z.ZodType>(data: T) =>
  z.object({ data, provenance: ProvenanceMap });

export const Group = z.enum(["cost_visible", "cost_restricted"]);
export type Group = z.infer<typeof Group>;

export const DocumentInfo = z.object({
  id: z.string(),
  name: z.string(),
  page_count: z.int().min(1),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  is_sample: z.boolean(),
  notice: z.string().nullable(),
});
export const DocumentResponse = envelope(DocumentInfo);
export type DocumentResponse = z.infer<typeof DocumentResponse>;

export const StageStatus = z.enum(["pending", "running", "done", "error"]);
export type StageStatus = z.infer<typeof StageStatus>;
export const JobStage = z.object({
  id: z.string(),
  label: z.string(),
  detail: z.string(),
  part: Part,
  status: StageStatus,
});
export type JobStage = z.infer<typeof JobStage>;
export const JobResponse = envelope(
  z.object({ started_at: z.int(), done: z.boolean(), stages: z.array(JobStage) }),
);
export type JobResponse = z.infer<typeof JobResponse>;

export const ClausesResponse = envelope(z.object({ group: Group, clauses: z.array(ClauseItem) }));
export type ClausesResponse = z.infer<typeof ClausesResponse>;

export const SentencesResponse = envelope(
  z.object({ page_number: z.int().min(1), sentences: z.array(Sentence) }),
);
export type SentencesResponse = z.infer<typeof SentencesResponse>;

export const RtmClauseRow = z.object({
  kind: z.literal("clause"),
  section_id: z.string(),
  title: z.string(),
  departments: z.array(z.string()),
  priority: Priority,
  red_flag: RedFlagType.nullable(),
  page_number: z.int().min(1),
  sentence_index: z.int().min(1),
  citation_status: CitationStatus,
});
export const RtmRow = z.discriminatedUnion("kind", [RtmClauseRow, MaskedClause]);
export type RtmRow = z.infer<typeof RtmRow>;
export const RtmResponse = envelope(z.object({ group: Group, rows: z.array(RtmRow) }));
export type RtmResponse = z.infer<typeof RtmResponse>;

export const CostField = z.enum(["min_quantity", "fixed_costs", "variable_cost_per_unit", "currency"]);
export type CostField = z.infer<typeof CostField>;
export const CostInputCandidate = z.object({
  field: CostField,
  label: z.string(),
  /** Decimal as string; null when the RFQ does not state it. */
  value: z.string().nullable(),
  note: z.string(),
  evidence: VerifiedFinding.nullable(),
});
export type CostInputCandidate = z.infer<typeof CostInputCandidate>;
export const CostInputsResponse = envelope(
  z.object({ item: z.string(), candidates: z.array(CostInputCandidate) }),
);
export type CostInputsResponse = z.infer<typeof CostInputsResponse>;

const decimalString = z.string().regex(/^\d+(\.\d+)?$/);
export const PrecostInputs = z.object({
  item: z.string().min(1),
  min_quantity: z.int().min(1),
  fixed_costs: decimalString,
  variable_cost_per_unit: decimalString,
  currency: z.string().regex(/^[A-Z]{3}$/),
});
export type PrecostInputs = z.infer<typeof PrecostInputs>;
export const PrecostRequest = z.object({ team: z.string().optional(), inputs: PrecostInputs });
export type PrecostRequest = z.infer<typeof PrecostRequest>;
export const PrecostResponse = envelope(
  z.object({
    inputs: PrecostInputs,
    fixed_cost_per_unit: decimalString,
    unit_cost: decimalString,
    decision: z.object({ status: z.literal("baseline_undefined"), note: z.string() }),
  }),
);
export type PrecostResponse = z.infer<typeof PrecostResponse>;

export const ChatRequest = z.object({ team: z.string().optional(), question: z.string().min(1).max(500) });
export type ChatRequest = z.infer<typeof ChatRequest>;
export const ChatResponse = envelope(
  z.object({
    status: z.enum(["answered", "not_prepared", "restricted"]),
    answer: VerifiedFinding.nullable(),
    message: z.string(),
    suggestions: z.array(z.string()),
  }),
);
export type ChatResponse = z.infer<typeof ChatResponse>;

export const EvaluationResponse = envelope(
  z.discriminatedUnion("status", [
    z.object({ status: z.literal("not_scored"), message: z.string() }),
    z.object({
      status: z.literal("scored"),
      scored_at: z.int(),
      metrics: z.array(
        z.object({ key: z.string(), label: z.string(), value: z.number(), target: z.number().nullable() }),
      ),
    }),
  ]),
);
export type EvaluationResponse = z.infer<typeof EvaluationResponse>;

export const Team = z.object({ id: z.string(), label: z.string(), group: Group });
export type Team = z.infer<typeof Team>;
export const MetaResponse = envelope(
  z.object({
    teams: z.array(Team),
    departments: z.array(z.object({ id: z.string(), label: z.string() })),
    assumption_note: z.string(),
  }),
);
export type MetaResponse = z.infer<typeof MetaResponse>;

export const UploadRequest = z.object({ file_name: z.string().max(255).nullable() });
export type UploadRequest = z.infer<typeof UploadRequest>;
export const UploadResponse = envelope(
  z.object({ id: z.string(), started_at: z.int(), notice: z.string() }),
);
export type UploadResponse = z.infer<typeof UploadResponse>;

export const ErrorResponse = z.object({ error: z.object({ code: z.string(), message: z.string() }) });
export type ErrorResponse = z.infer<typeof ErrorResponse>;

/** Every schema that is emitted to /contract. Keys become file names. */
export const CONTRACT_SCHEMAS = {
  locus: Locus,
  sentence: Sentence,
  clause: ClauseItem,
  document: DocumentResponse,
  job: JobResponse,
  clauses: ClausesResponse,
  sentences: SentencesResponse,
  rtm: RtmResponse,
  "cost-inputs": CostInputsResponse,
  "precost-request": PrecostRequest,
  precost: PrecostResponse,
  "chat-request": ChatRequest,
  chat: ChatResponse,
  evaluation: EvaluationResponse,
  meta: MetaResponse,
  "upload-request": UploadRequest,
  upload: UploadResponse,
  error: ErrorResponse,
} as const;
