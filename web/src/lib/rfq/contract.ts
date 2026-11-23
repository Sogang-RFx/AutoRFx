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
  work_areas: z.array(z.string()).optional(),
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

// ── Pre-cost and Go/No-Go ───────────────────────────────────────────────────
const decimalString = z.string().regex(/^\d+(\.\d+)?$/);
const signedDecimal = z.string().regex(/^-?\d+(\.\d+)?$/);
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/** What the RFQ itself can state for a pre-cost; the supplier's own figures are in the company settings. */
export const COST_FIELDS = [
  "currency",
  "volume",
  "target_price",
  "annual_reduction_rate",
  "payment_days",
  "incoterm",
  "thickness",
  "net_weight",
  "material",
] as const;
export const CostField = z.enum(COST_FIELDS);
export type CostField = z.infer<typeof CostField>;

export const ITEM_CATEGORIES = [
  "material",
  "process",
  "labor",
  "purchased",
  "outsourced",
  "logistics",
  "freight",
  "other",
  "credit",
] as const;
export const ItemCategory = z.enum(ITEM_CATEGORIES);
export type ItemCategory = z.infer<typeof ItemCategory>;

export const CostAlternative = z.object({ value: z.string(), evidence: VerifiedFinding });
export type CostAlternative = z.infer<typeof CostAlternative>;

export const CostInputCandidate = z.object({
  field: CostField,
  label: z.string(),
  unit: z.string().nullable(),
  /** Decimal or code as string; null when the RFQ does not state it or the files disagree. */
  value: z.string().nullable(),
  /** "rfq": stated in the RFQ. "missing": not stated. ("assumption" is kept for old artifacts.) */
  source: z.enum(["rfq", "assumption", "missing"]),
  note: z.string(),
  evidence: VerifiedFinding.nullable(),
  /** More than one entry when files state different values. */
  alternatives: z.array(CostAlternative),
  /** Consistency issue that must be resolved before this input can be used. */
  issue_id: z.string().nullable(),
});
export type CostInputCandidate = z.infer<typeof CostInputCandidate>;
export const CostInputs = z.object({
  item: z.string(),
  candidates: z.array(CostInputCandidate),
  /** Planned quantity per year, read from the RFQ's volume table; empty when none was found. */
  volumes: z.array(z.object({ year: z.int(), quantity: z.int().min(0) })),
  /** Fraction (0.03 = 3 %) or null when the RFQ states no annual reduction. */
  annual_reduction_rate: decimalString.nullable(),
  /** 1: the reduction starts in the second delivery year. */
  reduction_starts_at_index: z.int().min(0),
});
export type CostInputs = z.infer<typeof CostInputs>;
/** The cost-input artifact the pipeline stores for each RFQ. */
export const CostInputsResponse = envelope(CostInputs);
export type CostInputsResponse = z.infer<typeof CostInputsResponse>;

/** ③ One qualification or contract term the RFQ requires (knockout 1). */
export const RequirementCheck = z.object({
  /** Stable key, e.g. "certification:IATF16949"; answers are given by this key. */
  key: z.string(),
  requirement: z.string(),
  label: z.string(),
  department: z.string(),
  /** "fact": answered once and remembered. "judgment": asked every time. */
  kind: z.enum(["fact", "judgment"]),
  value: z.string(),
  question: z.string(),
  /** "ask": the user has to answer (setup only). After the calculation: pass, fail or unknown. */
  status: z.enum(["ask", "pass", "fail", "unknown"]),
  /** Where a settled status came from. */
  source: z.enum(["profile", "stored", "answer"]).nullable(),
  evidence: VerifiedFinding.nullable(),
});
export type RequirementCheck = z.infer<typeof RequirementCheck>;

export const Classification = z.object({
  /** manufacturing | distribution | construction | other */
  type: z.string(),
  label: z.string(),
  decision_unit: z.enum(["rfq", "item"]),
  baseline: z.enum(["target_price", "none"]),
  /** False: recognised but never judged automatically. */
  automatic: z.boolean(),
  /** Distinct patterns found per type. */
  signals: z.record(z.string(), z.int()),
  note: z.string(),
});
export type Classification = z.infer<typeof Classification>;

/** A cost item: quantity per piece × unit price. Credit items are subtracted. */
export const CostItem = z.object({
  label: z.string().min(1).max(80),
  category: ItemCategory,
  quantity: decimalString,
  unit: z.string().min(1).max(16),
  unit_price: decimalString,
  /** Price-table row the unit price came from; null when typed in. */
  price_id: z.string().nullable(),
});
export type CostItem = z.infer<typeof CostItem>;

const PriceSheet = z.object({
  source: z.string(), revised: z.boolean(), tax: z.string().nullable(),
  items: z.array(z.object({
    category: z.string(), item: z.string(), part: z.string(), updated_part: z.string(),
    description: z.string(), quantity: z.int().nonnegative(), page: z.int().positive(), location: z.string().nullable().optional(),
  })),
});
export type PriceSheet = z.infer<typeof PriceSheet>;

export const PrecostSetupResponse = envelope(
  z.object({
    price_sheets: z.array(PriceSheet).optional(),
    classification: Classification,
    requirements: z.array(RequirementCheck),
    /** Clauses that pass a shock to the customer; evidence is null when the RFQ has none. */
    pass_through: z.array(z.object({ id: z.string(), label: z.string(), evidence: VerifiedFinding.nullable() })),
    cost_inputs: CostInputs,
    suggested_items: z.array(CostItem.extend({ expired: z.boolean(), note: z.string() })),
    assumption_note: z.string(),
  }),
);
export type PrecostSetupResponse = z.infer<typeof PrecostSetupResponse>;

// Company settings: the supplier's own figures, entered once (initial setup).
export const PriceRow = z.object({
  id: z.string().regex(/^price-[0-9a-f]{12}$/),
  category: ItemCategory,
  name: z.string().min(1).max(80),
  unit: z.string().min(1).max(16),
  price: decimalString,
  currency: z.string().regex(/^[A-Z]{3}$/),
  valid_until: isoDate,
  department: z.string(),
});
export type PriceRow = z.infer<typeof PriceRow>;
export const CompanySettings = z.object({
  home_currency: z.string().regex(/^[A-Z]{3}$/),
  discount_rate: decimalString,
  min_margin: decimalString,
  scrap_rate: decimalString,
  sga_rate: decimalString,
  max_payment_days: z.int().min(0).max(365).nullable(),
  max_liability: decimalString.nullable(),
});
export type CompanySettings = z.infer<typeof CompanySettings>;
export const CompanyProfile = z.object({
  certifications: z.array(z.string()),
  processes: z.array(z.string()),
  qualifications: z.array(z.string()),
});
export type CompanyProfile = z.infer<typeof CompanyProfile>;
export const CompanyResponse = envelope(
  z.object({
    settings: CompanySettings,
    profile: CompanyProfile,
    prices: z.array(PriceRow).max(300),
    answers: z.array(z.object({ key: z.string(), label: z.string(), answer: z.enum(["yes", "no", "unknown"]), saved_on: isoDate })),
    today: isoDate,
    categories: z.record(z.string(), z.string()),
  }),
);
export type CompanyResponse = z.infer<typeof CompanyResponse>;
/** Body of `PUT /company`: the whole record. A price row with `id: null` is new. Stored answers are kept. */
export const CompanyRequest = z.object({
  settings: CompanySettings,
  profile: CompanyProfile,
  prices: z.array(PriceRow.extend({ id: z.string().nullable() })),
});
export type CompanyRequest = z.infer<typeof CompanyRequest>;

export const PrecostInputs = z.object({
  item: z.string().min(1).max(200),
  currency: z.string().regex(/^[A-Z]{3}$/),
  target_price: decimalString.nullable(),
  volumes: z.array(z.object({ year: z.int().min(2000).max(2100), quantity: z.int().min(0) })).min(1).max(30),
  /** Fraction: 0.03 = 3 % per year. */
  annual_reduction_rate: decimalString,
  reduction_starts_at_index: z.int().min(0).max(30),
  payment_days: z.int().min(0).max(365).nullable(),
  items: z.array(CostItem).min(1).max(60),
  tooling: z.object({
    amount: decimalString,
    payment: z.enum(["lump_sum", "amortized", "deferred"]),
    deferred_years: z.int().min(0).max(10),
  }),
  /** null: as found in the RFQ. */
  material_indexed: z.boolean().nullable(),
  fx_adjusted: z.boolean().nullable(),
  /** Peak yearly volume ÷ available capacity, e.g. 0.85. */
  capacity_utilization: decimalString.nullable(),
  strategic: z.int().min(0).max(100).nullable(),
  /** Knockout answers by question key. */
  answers: z.record(z.string(), z.enum(["yes", "no", "unknown"])),
});
export type PrecostInputs = z.infer<typeof PrecostInputs>;

export const PrecostRequest = z.object({
  team: z.string().optional(),
  /** When given, the RFQ's type, knockout questions, clauses and cost conflicts are taken into account. */
  rfq_id: z.string().nullable(),
  inputs: PrecostInputs,
  /** The value the user chose for each resolved issue: issue id → value. */
  resolved: z.record(z.string(), z.string()),
  /** Keep yes/no answers to fact questions for the next RFQ. */
  remember: z.boolean().optional(),
});
export type PrecostRequest = z.infer<typeof PrecostRequest>;

export const DecisionStatus = z.enum(["go", "conditional", "no_go", "hold", "baseline_undefined", "not_applicable"]);
export type DecisionStatus = z.infer<typeof DecisionStatus>;

export const PrecostResponse = envelope(
  z.object({
    item: z.string(),
    currency: z.string(),
    classification: Classification.nullable(),
    knockout: z.object({
      status: z.enum(["passed", "open", "failed"]),
      checks: z.array(RequirementCheck.extend({ answer: z.enum(["yes", "no", "unknown"]).nullable() })),
    }),
    cost: z.object({
      lines: z.array(z.object({ category: z.string(), label: z.string(), value: signedDecimal })),
      /** manufacturing: scrap and SG&A uplift; resale: bought-in goods, no uplift. */
      cost_basis: z.enum(["manufacturing", "resale"]),
      piece_cost: decimalString,
      tooling_per_piece: decimalString.nullable(),
      lifetime_factor: decimalString,
      material_indexed: z.boolean(),
      /** Least favourable case of the unresolved conflicts. */
      min_quote: decimalString,
      safe_quote: decimalString,
      /** Units over the whole contract and the revenue at each quote (Σ quantity × price after reductions). */
      total_quantity: z.int(),
      totals: z.object({ min_quote: decimalString, safe_quote: decimalString }),
      /** Resale only: quote over the purchase cost, as a fraction (0.12 = 12 % markup). */
      markup: z.object({ purchased: decimalString, min_quote: signedDecimal, safe_quote: signedDecimal }).nullable(),
      scenarios: z.array(
        z.object({
          label: z.string(),
          choices: z.array(z.object({ issue_id: z.string(), value: z.string() })),
          piece_cost: decimalString,
          min_quote: decimalString,
        }),
      ),
      shocks: z.array(
        z.object({ id: z.string(), label: z.string(), applied: z.boolean(), note: z.string(), min_quote_delta: signedDecimal.nullable() }),
      ),
      /** Reference only: never decides. */
      npv: z.object({
        value: signedDecimal,
        discount_rate: decimalString,
        price_basis: z.enum(["target_price", "min_quote"]),
        payback_year: z.int().nullable(),
      }),
      yearly: z.array(z.object({ year: z.int(), quantity: z.int(), price: decimalString })),
    }),
    score: z
      .object({
        total: z.int().min(0).max(100),
        band: z.enum(["go", "conditional", "no_go"]),
        items: z.array(z.object({ id: z.string(), label: z.string(), weight: z.int(), score: z.int(), note: z.string() })),
      })
      .nullable(),
    decision: z.object({
      status: DecisionStatus,
      label: z.string(),
      /** What decided: knockout | rfq_type | unmodelled_conflict | no_target | price_knockout | conflict_dependent | score */
      rule: z.string(),
      reasons: z.array(z.string()),
      score: z.int().min(0).max(100).nullable(),
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
    work_areas: z.array(z.object({ id: z.string(), label: z.string() })).optional(),
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
  "precost-setup": PrecostSetupResponse,
  "precost-request": PrecostRequest,
  precost: PrecostResponse,
  company: CompanyResponse,
  "company-request": CompanyRequest,
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
