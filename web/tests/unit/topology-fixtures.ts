/** Builders for the topology tests. Every name, title and number is invented. */
import type {
  ClauseItem,
  ClausesResponse,
  ConsistencyResponse,
  DocumentResponse,
  IssueItem,
  Locus,
  PackageFile,
  Priority,
} from "@/lib/rfq/contract";

const HASH = "a".repeat(64);

export function packageFile(id: string, name = `${id}.pdf`): PackageFile {
  return {
    id,
    name,
    kind: "pdf",
    lang: "ko",
    page_count: 3,
    sha256: HASH,
    canonical_tool: "identity",
    canonical_sha256: HASH,
    clause_count: 0,
    warnings: [],
  };
}

export function documentOf(...fileIds: string[]): DocumentResponse["data"] {
  return {
    id: "rfq-test",
    name: "시험 패키지",
    files: fileIds.map((id) => packageFile(id)),
    page_count: 3 * fileIds.length,
    sha256: HASH,
    is_sample: false,
    notice: null,
  };
}

export interface ClauseSpec {
  id: string;
  file: string;
  lead: string;
  refs?: string[];
  priority?: Priority;
  flag?: string;
  title?: string;
}

export function visibleClause(spec: ClauseSpec): ClauseItem {
  const locus: Locus = {
    source_doc: spec.file,
    page_number: 1,
    section_id: spec.id,
    paragraph_index: 1,
    sentence_index: 1,
    anchor: null,
  };
  return {
    kind: "clause",
    clause_id: `${spec.file}:${spec.id}`,
    source_doc: spec.file,
    section_id: spec.id,
    section_path: [spec.id],
    title: spec.title ?? `조항 ${spec.id}`,
    page_number: 1,
    lang: "ko",
    primary_department: spec.lead,
    reference_departments: spec.refs ?? [],
    priority: spec.priority ?? "should",
    red_flag: spec.flag ? { type: "x", label: spec.flag, reason: "시험용 사유" } : null,
    excerpt: [],
    summary: {
      finding: {
        kind: "summary",
        departments: [spec.lead],
        text: "시험용 요약",
        priority: null,
        red_flag_reason: null,
        citations: [locus],
      },
      citation_status: "verified",
      failed_citations: [],
    },
    translation_ko: null,
    origin: "rule",
  };
}

export function maskedClause(id: string, file: string): ClauseItem {
  return { kind: "masked", clause_id: `${file}:${id}`, source_doc: file, section_id: id };
}

export function clausesOf(...items: ClauseItem[]): ClausesResponse["data"] {
  return { group: "cost_restricted", clauses: items };
}

export function consistencyOf(
  issues: IssueItem[],
  group: "cost_visible" | "cost_restricted" = "cost_visible",
): ConsistencyResponse["data"] {
  return { group, issues, checked: { files: 2, entities: 1, references: 1 } };
}

export function visibleIssue(id: string, ...files: string[]): IssueItem {
  return {
    kind: "issue",
    id,
    category: "conflict",
    severity: "medium",
    entity: null,
    title: `이슈 ${id}`,
    detail: "시험용",
    affects_cost: false,
    departments: [],
    evidence: files.map((f) => ({
      locus: { source_doc: f, page_number: 1, section_id: "1", paragraph_index: 1, sentence_index: 1, anchor: null },
      text: "시험용",
      value: null,
      normalized: null,
    })),
    citation_status: "verified",
  };
}

export const DEPARTMENTS = [
  { id: "quality", label: "품질" },
  { id: "purchasing", label: "구매" },
  { id: "legal", label: "법무" },
];
