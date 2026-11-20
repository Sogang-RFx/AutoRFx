/**
 * AutoRFx response contract, version 2 (SPEC §4, §13).
 *
 * Single source of truth: these zod schemas. `pnpm contract:emit` writes the
 * JSON Schema files in `/contract`; the Python backend validates its responses
 * against those files (tests/contract). The unit test fails when the committed
 * JSON Schema drifts from these definitions.
 *
 * v2 (2026-11-18): an RFQ is a package of files; a clause has one lead
 * department and reference departments; cross-document consistency issues;
 * engine indicator (rule-based vs. LLM); cost breakdown and Go/No-Go.
 */
import { z } from "zod";

export const CONTRACT_VERSION = 2;

// ── Parts and provenance ────────────────────────────────────────────────────
export const PARTS = [
  "document",
  "parsing",
  "analysis",
  "citation",
  "translation",
  "source",
  "consistency",
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

/**
 * How the real parts were computed. `rule-only` means no LLM is connected and
 * every judgement comes from the rule files; the screen says so.
 */
export const Engines = z.object({
  /** "fixture" | "rule-only" | "openai:<model>" */
  analysis: z.string().min(1),
  /** "fixture" | "none" | "deepl" | "openai:<model>" */
  translation: z.string().min(1),
  /** "scripted" | "bm25" | "bm25+dense:<model>" */
  retrieval: z.string().min(1),
  llm_connected: z.boolean(),
});
export type Engines = z.infer<typeof Engines>;

/** Where one clause's judgement came from. */
export const Origin = z.enum(["fixture", "rule", "llm", "rule_fallback"]);
export type Origin = z.infer<typeof Origin>;

// ── Location model ──────────────────────────────────────────────────────────
export const Locus = z.object({
  /** File id inside the package, e.g. "d01". */
  source_doc: z.string().min(1),
  /** Page of that file's canonical PDF, from 1. */
  page_number: z.int().min(1),
  section_id: z.string().min(1),
  paragraph_index: z.int().min(1),
  sentence_index: z.int().min(1),
  /** Spreadsheet cell or table position, e.g. "Part Data!C6"; null for running text. */
  anchor: z.string().nullable(),
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

/** Type ids and labels come from data/rules/red_flags.yaml, not from code. */
export const RedFlagRef = z.object({ type: z.string().min(1), label: z.string().min(1) });
export type RedFlagRef = z.infer<typeof RedFlagRef>;
export const RedFlag = RedFlagRef.extend({ reason: z.string() });
export type RedFlag = z.infer<typeof RedFlag>;

export const CitationStatus = z.enum(["verified", "unverified"]);
export type CitationStatus = z.infer<typeof CitationStatus>;

export const Finding = z.object({
  kind: z.enum(["summary", "priority", "red_flag", "answer", "cost_input", "consistency"]),
  /** Lead department first, then reference departments. */
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

export const Lang = z.enum(["en", "de", "ko", "mixed", "unknown"]);
export type Lang = z.infer<typeof Lang>;

export const VisibleClause = z.object({
  kind: z.literal("clause"),
  /** Unique inside the RFQ, e.g. "d01:II.G". */
  clause_id: z.string().min(1),
  source_doc: z.string().min(1),
  section_id: z.string(),
  section_path: z.array(z.string()),
  title: z.string(),
  page_number: z.int().min(1),
  lang: Lang,
  primary_department: z.string().min(1),
  reference_departments: z.array(z.string()),
  priority: Priority,
  red_flag: RedFlag.nullable(),
  excerpt: z.array(SourceExcerpt),
  summary: VerifiedFinding,
  translation_ko: z.string().nullable(),
  origin: Origin,
});
export type VisibleClause = z.infer<typeof VisibleClause>;

/** INV-8: a masked clause carries its identity and the flag, nothing else. */
export const MaskedClause = z.strictObject({
  kind: z.literal("masked"),
  clause_id: z.string().min(1),
  source_doc: z.string().min(1),
  section_id: z.string(),
});
export type MaskedClause = z.infer<typeof MaskedClause>;

export const ClauseItem = z.discriminatedUnion("kind", [VisibleClause, MaskedClause]);
export type ClauseItem = z.infer<typeof ClauseItem>;

// ── Responses ───────────────────────────────────────────────────────────────
const envelope = <T extends z.ZodType>(data: T) => z.object({ data, provenance: ProvenanceMap });

export const Group = z.enum(["cost_visible", "cost_restricted"]);
export type Group = z.infer<typeof Group>;

const sha256 = z.string().regex(/^[0-9a-f]{64}$/);

export const FileKind = z.enum(["pdf", "doc", "docx", "xlsx"]);
export type FileKind = z.infer<typeof FileKind>;

export const PackageFile = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  kind: FileKind,
  lang: Lang,
  /** Pages of the canonical PDF. */
  page_count: z.int().min(1),
  /** Hash of the original file, which is never modified. */
  sha256,
  /** How the canonical PDF was made: "identity" for PDF originals, else tool and version. */
  canonical_tool: z.string().min(1),
  canonical_sha256: sha256,
  clause_count: z.int().min(0),
  /** Things the reader must know, e.g. pages without a text layer. */
  warnings: z.array(z.string()),
});
export type PackageFile = z.infer<typeof PackageFile>;

export const DocumentInfo = z.object({
  id: z.string(),
  name: z.string(),
  files: z.array(PackageFile).min(1),
  /** Sum over files. */
  page_count: z.int().min(1),
  /** Digest over the sorted (name, sha256) pairs of the files. */
  sha256,
  is_sample: z.boolean(),
  notice: z.string().nullable(),
});
export const DocumentResponse = envelope(DocumentInfo);
export type DocumentResponse = z.infer<typeof DocumentResponse>;

export const StageStatus = z.enum(["pending", "running", "done", "error", "skipped"]);
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
  z.object({ source_doc: z.string().min(1), page_number: z.int().min(1), sentences: z.array(Sentence) }),
);
export type SentencesResponse = z.infer<typeof SentencesResponse>;

export const RtmClauseRow = z.object({
  kind: z.literal("clause"),
  clause_id: z.string().min(1),
  source_doc: z.string().min(1),
  section_id: z.string(),
  title: z.string(),
  primary_department: z.string().min(1),
  reference_departments: z.array(z.string()),
  priority: Priority,
  red_flag: RedFlagRef.nullable(),
  page_number: z.int().min(1),
  sentence_index: z.int().min(1),
  anchor: z.string().nullable(),
  citation_status: CitationStatus,
});
export const RtmRow = z.discriminatedUnion("kind", [RtmClauseRow, MaskedClause]);
export type RtmRow = z.infer<typeof RtmRow>;
export const RtmResponse = envelope(z.object({ group: Group, rows: z.array(RtmRow) }));
export type RtmResponse = z.infer<typeof RtmResponse>;

// ── Cross-document consistency ──────────────────────────────────────────────
export const IssueCategory = z.enum(["conflict", "missing_attachment", "broken_reference", "notation"]);
export type IssueCategory = z.infer<typeof IssueCategory>;

export const IssueEvidence = z.object({
  locus: Locus,
  text: z.string(),
  /** The value as written, and its normalized form used for comparison. */
  value: z.string().nullable(),
  normalized: z.string().nullable(),
});
export type IssueEvidence = z.infer<typeof IssueEvidence>;

export const VisibleIssue = z.object({
  kind: z.literal("issue"),
  id: z.string().min(1),
  category: IssueCategory,
  severity: z.enum(["high", "medium", "low"]),
  /** Entity id from data/rules/entities.yaml; null for reference and attachment issues. */
  entity: z.string().nullable(),
  title: z.string(),
  detail: z.string(),
  /** True when the issue changes a cost input, so the quote decision must wait. */
  affects_cost: z.boolean(),
  departments: z.array(z.string()),
  evidence: z.array(IssueEvidence),
  citation_status: CitationStatus,
});
export type VisibleIssue = z.infer<typeof VisibleIssue>;

/** INV-8: an issue whose evidence is cost-sensitive shows only that it exists. */
export const MaskedIssue = z.strictObject({ kind: z.literal("masked"), id: z.string().min(1) });
export type MaskedIssue = z.infer<typeof MaskedIssue>;

export const IssueItem = z.discriminatedUnion("kind", [VisibleIssue, MaskedIssue]);
export type IssueItem = z.infer<typeof IssueItem>;

export const ConsistencyResponse = envelope(
  z.object({
    group: Group,
    issues: z.array(IssueItem),
    /** What was compared, so "no issues" can be told apart from "nothing checked". */
    checked: z.object({ files: z.int().min(0), entities: z.int().min(0), references: z.int().min(0) }),
  }),
);
export type ConsistencyResponse = z.infer<typeof ConsistencyResponse>;

// ── Pre-cost ────────────────────────────────────────────────────────────────
const decimalString = z.string().regex(/^\d+(\.\d+)?$/);

/** Top-level inputs (SPEC §8.1) and the line items of the cost breakdown (§8.2). */
export const COST_FIELDS = [
  "currency",
  "min_quantity",
  "fixed_costs",
  "variable_cost_per_unit",
  "target_price",
  "annual_reduction_rate",
  "gross_weight",
  "net_weight",
  "material_price",
  "scrap_credit",
  "machine_hour_rate",
  "strokes_per_minute",
  "parts_per_stroke",
  "oee",
  "surface_cost",
  "packaging_cost",
  "freight_cost",
  "warehousing_cost",
  "scrap_rate",
  "sga_rate",
  "profit_rate",
  "thickness",
  "incoterm",
  "payment_days",
] as const;
export const CostField = z.enum(COST_FIELDS);
export type CostField = z.infer<typeof CostField>;

export const CostAlternative = z.object({ value: z.string(), evidence: VerifiedFinding });
export type CostAlternative = z.infer<typeof CostAlternative>;

export const CostInputCandidate = z.object({
  field: CostField,
  label: z.string(),
  unit: z.string().nullable(),
  /** Decimal or code as string; null when the RFQ does not state it or the files disagree. */
  value: z.string().nullable(),
  /** "rfq": stated in the RFQ. "assumption": supplier's own figure. "missing": neither. */
  source: z.enum(["rfq", "assumption", "missing"]),
  note: z.string(),
  evidence: VerifiedFinding.nullable(),
  /** More than one entry when files state different values. */
  alternatives: z.array(CostAlternative),
  /** Consistency issue that must be resolved before this input can be used. */
  issue_id: z.string().nullable(),
});
export type CostInputCandidate = z.infer<typeof CostInputCandidate>;
export const CostInputsResponse = envelope(
  z.object({ item: z.string(), candidates: z.array(CostInputCandidate) }),
);
export type CostInputsResponse = z.infer<typeof CostInputsResponse>;

export const PrecostInputs = z.object({
  item: z.string().min(1),
  min_quantity: z.int().min(1),
  fixed_costs: decimalString,
  variable_cost_per_unit: decimalString,
  currency: z.string().regex(/^[A-Z]{3}$/),
  target_price: decimalString.nullable(),
});
export type PrecostInputs = z.infer<typeof PrecostInputs>;

/** Annex-style cost breakdown. Rates are fractions (0.05 = 5 %), OEE included. */
export const BreakdownInputs = z.object({
  gross_weight: decimalString,
  net_weight: decimalString,
  material_price: decimalString,
  scrap_credit: decimalString,
  machine_hour_rate: decimalString,
  strokes_per_minute: decimalString,
  parts_per_stroke: decimalString,
  oee: decimalString,
  surface_cost: decimalString,
  packaging_cost: decimalString,
  freight_cost: decimalString,
  warehousing_cost: decimalString,
  scrap_rate: decimalString,
  sga_rate: decimalString,
  profit_rate: decimalString,
});
export type BreakdownInputs = z.infer<typeof BreakdownInputs>;

export const PrecostRequest = z.object({
  team: z.string().optional(),
  /** When given, unresolved cost-related consistency issues of this RFQ are taken into account. */
  rfq_id: z.string().nullable(),
  inputs: PrecostInputs,
  /** When given, the variable cost is computed from it and `inputs.variable_cost_per_unit` is ignored. */
  breakdown: BreakdownInputs.nullable(),
  /** The value the user chose for each resolved issue: issue id → value. */
  resolved: z.record(z.string(), z.string()),
});
export type PrecostRequest = z.infer<typeof PrecostRequest>;

export const DecisionStatus = z.enum(["go", "no_go", "hold", "baseline_undefined"]);
export type DecisionStatus = z.infer<typeof DecisionStatus>;

export const PrecostResponse = envelope(
  z.object({
    /** Inputs as used: `variable_cost_per_unit` is the computed one when a breakdown was sent. */
    inputs: PrecostInputs,
    breakdown: z
      .object({
        lines: z.array(z.object({ key: z.string(), label: z.string(), value: decimalString })),
        piece_cost: decimalString,
        piece_price: decimalString,
      })
      .nullable(),
    fixed_cost_per_unit: decimalString,
    unit_cost: decimalString,
    /** One entry per combination of unresolved cost-driver values; empty when nothing is unresolved. */
    scenarios: z.array(
      z.object({
        label: z.string(),
        choices: z.array(z.object({ issue_id: z.string(), value: z.string() })),
        unit_cost: decimalString,
      }),
    ),
    range: z.object({ low: decimalString, high: decimalString }),
    /** Target price per delivery year after the annual reduction; empty when not stated. */
    yearly: z.array(z.object({ year: z.int(), price: decimalString, below_cost: z.boolean() })),
    decision: z.object({
      status: DecisionStatus,
      note: z.string(),
      reasons: z.array(z.string()),
      target_price: decimalString.nullable(),
      /** target_price − range.high; negative as "-0.0050". */
      gap: z.string().nullable(),
      /** Id of the rule in data/rules/go_no_go.yaml that decided. */
      rule: z.string(),
    }),
  }),
);
export type PrecostResponse = z.infer<typeof PrecostResponse>;

// ── Chat ────────────────────────────────────────────────────────────────────
export const ChatRequest = z.object({ team: z.string().optional(), question: z.string().min(1).max(500) });
export type ChatRequest = z.infer<typeof ChatRequest>;
export const ChatResponse = envelope(
  z.object({
    /** `no_evidence`: nothing in the RFQ supports an answer. `not_prepared`: mock only. */
    status: z.enum(["answered", "no_evidence", "not_prepared", "restricted"]),
    /** "scripted" (mock), "extractive" (original sentences, no LLM), "generative" (LLM). */
    mode: z.enum(["scripted", "extractive", "generative"]),
    answer: VerifiedFinding.nullable(),
    message: z.string(),
    suggestions: z.array(z.string()),
  }),
);
export type ChatResponse = z.infer<typeof ChatResponse>;

// ── Evaluation ──────────────────────────────────────────────────────────────
export const EvaluationResponse = envelope(
  z.discriminatedUnion("status", [
    z.object({ status: z.literal("not_scored"), message: z.string() }),
    z.object({
      status: z.literal("scored"),
      scored_at: z.int(),
      /** What was scored, e.g. the answer key file name. Held-out sets are never scored here. */
      dataset: z.string(),
      metrics: z.array(
        z.object({
          key: z.string(),
          label: z.string(),
          value: z.number(),
          target: z.number().nullable(),
          support: z.int().nullable(),
        }),
      ),
      errors: z.array(
        z.object({ dimension: z.string(), ref: z.string(), expected: z.string(), actual: z.string() }),
      ),
    }),
  ]),
);
export type EvaluationResponse = z.infer<typeof EvaluationResponse>;

// ── Meta and upload ─────────────────────────────────────────────────────────
export const Team = z.object({
  id: z.string(),
  label: z.string(),
  group: Group,
  /** A company department (SPEC US10) lists the rule departments it receives; a rule team has none. */
  areas: z.array(z.string()).optional(),
});
export type Team = z.infer<typeof Team>;

// ── Company departments (SPEC US10) ─────────────────────────────────────────
/**
 * A department the company defined. `areas` are rule department ids ("업무 영역");
 * the clause filter is their union. Cost visibility is off unless switched on.
 */
export const OrgDepartment = z.object({
  id: z.string().regex(/^org-[0-9a-f]{12}$/),
  label: z.string().min(1).max(40),
  areas: z.array(z.string()).min(1),
  cost_visible: z.boolean(),
});
export type OrgDepartment = z.infer<typeof OrgDepartment>;
export const OrgDepartmentsResponse = envelope(z.object({ departments: z.array(OrgDepartment).max(30) }));
export type OrgDepartmentsResponse = z.infer<typeof OrgDepartmentsResponse>;
/** Body of `PUT /org/departments`: the whole list. `id: null` is a new department. */
export const OrgDepartmentsRequest = z.object({
  departments: z.array(
    z.object({
      id: z.string().nullable(),
      label: z.string(),
      areas: z.array(z.string()),
      cost_visible: z.boolean(),
    }),
  ),
});
export type OrgDepartmentsRequest = z.infer<typeof OrgDepartmentsRequest>;
export const Department = z.object({
  id: z.string(),
  label: z.string(),
  /** What the department does with an RFQ, from data/rules/departments.yaml. */
  role: z.string(),
  /** True for the department that receives every clause of every RFQ. */
  receives_all: z.boolean(),
});
export type Department = z.infer<typeof Department>;
export const MetaResponse = envelope(
  z.object({
    teams: z.array(Team),
    departments: z.array(Department),
    assumption_note: z.string(),
    engines: Engines,
    /** Packages already on the server that can be opened without an upload. */
    samples: z.array(z.object({ id: z.string(), label: z.string(), file_count: z.int().min(1) })),
    upload: z.object({ max_files: z.int().min(1), max_file_mb: z.int().min(1), accept: z.array(z.string()) }),
  }),
);
export type MetaResponse = z.infer<typeof MetaResponse>;

/**
 * JSON body of `POST /rfqs`. Mock mode sends file names only and never the
 * files. A real backend also accepts multipart/form-data with `files` parts.
 */
export const UploadRequest = z.object({
  file_names: z.array(z.string().max(255)).max(50),
  sample_id: z.string().nullable(),
});
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
  issue: IssueItem,
  document: DocumentResponse,
  job: JobResponse,
  clauses: ClausesResponse,
  sentences: SentencesResponse,
  rtm: RtmResponse,
  consistency: ConsistencyResponse,
  "cost-inputs": CostInputsResponse,
  "precost-request": PrecostRequest,
  precost: PrecostResponse,
  "chat-request": ChatRequest,
  chat: ChatResponse,
  evaluation: EvaluationResponse,
  meta: MetaResponse,
  "org-departments": OrgDepartmentsResponse,
  "org-departments-request": OrgDepartmentsRequest,
  "upload-request": UploadRequest,
  upload: UploadResponse,
  error: ErrorResponse,
} as const;
