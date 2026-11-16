"use client";
/**
 * Evaluation (TASKS S017, US3). Until the domain team's answer key is scored
 * this is an honest empty state: it lists what will be measured, with no
 * example numbers (SPEC §13.4).
 */
import { useParams } from "next/navigation";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/ui/primitives";
import { rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

const PLANNED = [
  { name: "부서 분류", how: "정답지의 담당 부서와 비교한 정밀도·재현율" },
  { name: "요건 강도", how: "Must / Should / Optional / Info 판정 일치율" },
  { name: "독소조항", how: "정답지에 표시된 독소조항을 찾아낸 비율" },
  { name: "인용 정확도", how: "결과가 가리킨 문장이 정답지의 근거 문장과 같은 비율" },
];

export default function EvaluationPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const session = useSession();
  const evaluation = useResource(session.ready ? `eval:${rfqId}` : null, (s) => rfqApi.evaluation(rfqId, s));

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-3 md:p-6">
      <div>
        <h1 className="text-title font-semibold text-ink">평가</h1>
        <p className="text-body text-ink-muted">도메인 팀이 만든 정답지로 분석 결과를 채점합니다.</p>
      </div>
      <Panel
        title="채점 결과"
        labelId="eval-title"
        provenance={evaluation.status === "ok" ? evaluation.data.provenance : undefined}
      >
        {evaluation.status === "loading" && <Skeleton lines={4} />}
        {evaluation.status === "error" && <ErrorState message={evaluation.error} />}
        {evaluation.status === "ok" && evaluation.data.data.status === "not_scored" && (
          <div data-testid="eval-empty">
            <EmptyState title="채점 전">
              <p>{evaluation.data.data.message}</p>
            </EmptyState>
            <div className="overflow-x-auto">
              <table className="w-full border-t border-line text-left text-dense">
                <caption className="px-4 pt-4 text-left text-label font-semibold text-ink-muted">측정할 지표</caption>
                <thead className="text-label text-ink-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2">지표</th>
                    <th scope="col" className="px-3 py-2">측정 방법</th>
                    <th scope="col" className="px-3 py-2">값</th>
                  </tr>
                </thead>
                <tbody>
                  {PLANNED.map((m) => (
                    <tr key={m.name} className="border-t border-line">
                      <td className="px-4 py-2.5 font-medium text-ink">{m.name}</td>
                      <td className="px-3 py-2.5 text-ink-muted">{m.how}</td>
                      <td className="px-3 py-2.5 text-ink-muted">채점 전</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {evaluation.status === "ok" && evaluation.data.data.status === "scored" && (
          <table className="w-full text-left text-dense tabular">
            <tbody>
              {evaluation.data.data.metrics.map((m) => (
                <tr key={m.key} className="border-b border-line">
                  <td className="px-4 py-2.5 font-medium text-ink">{m.label}</td>
                  <td className="px-3 py-2.5 text-ink">{m.value}</td>
                  <td className="px-3 py-2.5 text-ink-muted">{m.target === null ? "목표 미정" : `목표 ${m.target}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
