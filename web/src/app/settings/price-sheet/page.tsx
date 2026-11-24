"use client";
/**
 * Price-sheet dictionary candidates. When a user assigns the columns of a table the
 * dictionary (data/rules/price_sheet.yaml) could not read, the header words become
 * candidates here. Nothing changes until a candidate is approved: an approved word is then
 * used for every table, like a word of the rules file. The domain team moves approved
 * words into price_sheet.yaml when it reviews the rules. Cost-visible teams only, like the
 * pre-cost screens that produce the candidates.
 */
import Link from "next/link";
import { useState } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { ROLE_LABEL } from "@/components/precost/sheet-tables";
import { IconLock } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, ForbiddenState, Panel, Skeleton } from "@/components/ui/primitives";
import type { SheetWordRequest, SheetWordsResponse } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

type Words = SheetWordsResponse["data"];

export default function PriceSheetWordsPage() {
  const session = useSession();
  const { restricted, ready } = useTeamGroup();
  const loaded = useResource(ready && !restricted ? `sheet-words:${session.team}` : null, (s) =>
    rfqApi.sheetWords({ team: session.team }, s),
  );
  const [words, setWords] = useState<Words | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = words ?? (loaded.status === "ok" ? loaded.data.data : null);

  const decide = async (body: SheetWordRequest) => {
    setBusy(true);
    setError(null);
    try {
      setWords((await rfqApi.decideSheetWord(body, { team: session.team })).data);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const header = (
    <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
      <Link href="/" className="font-serif text-heading font-semibold text-ink">
        AutoRFx
      </Link>
      <h1 className="text-heading font-semibold text-ink">가격표 사전</h1>
      <button type="button" onClick={() => history.back()} className="ml-auto text-label text-ink-muted hover:text-ink">
        ← 돌아가기
      </button>
    </header>
  );

  if (ready && restricted) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-4xl flex-col gap-6 px-4 py-8 md:px-8">
        {header}
        <Panel title="가격표 사전" labelId="sheet-words-title">
          <EmptyState title="이 팀에는 원가 정보가 제공되지 않습니다">
            <p className="flex items-center gap-1.5">
              <IconLock size={14} /> 금액 열람이 허용된 팀을 고르면 볼 수 있습니다.
            </p>
          </EmptyState>
        </Panel>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-4xl flex-col gap-6 px-4 py-8 md:px-8">
      {header}
      <p className="text-body text-ink-muted">
        사전원가 화면에서 읽지 못한 표의 열을 직접 지정하면, 그 머리글 단어가 여기 후보로 올라옵니다. 그 양식은 이미 기억되어
        다음에도 그대로 읽힙니다. 후보를 <span className="font-semibold text-ink">승인</span>하면 다른 양식의 표에서도 그 단어로
        열을 찾습니다. 잘못 지정한 단어는 거절하세요.
      </p>
      {!ready || loaded.status === "loading" ? <Skeleton lines={4} /> : null}
      {loaded.status === "error" &&
        (loaded.forbidden ? <ForbiddenState message={loaded.error} /> : <ErrorState message={loaded.error} />)}
      {error && <ErrorState message={error} />}
      {shown && (
        <>
          <Panel title={`승인 대기 ${shown.candidates.length}개`} labelId="candidates-title" provenance={{ precost: "real" }}>
            {shown.candidates.length === 0 ? (
              <EmptyState title="후보가 없습니다" />
            ) : (
              <table className="w-full text-left text-dense" data-testid="word-candidates">
                <thead>
                  <tr className="text-label text-ink-muted">
                    <th className="p-2 font-semibold">머리글 단어</th>
                    <th className="p-2 font-semibold">지정한 열</th>
                    <th className="p-2 font-semibold">지정 횟수</th>
                    <th className="p-2 font-semibold">마지막</th>
                    <th className="p-2" />
                  </tr>
                </thead>
                <tbody>
                  {shown.candidates.map((c) => (
                    <tr key={`${c.role}:${c.text}`} className="border-t border-line" data-testid={`candidate-${c.role}-${c.text}`}>
                      <td className="p-2 font-semibold text-ink">{c.text}</td>
                      <td className="p-2 text-ink">{ROLE_LABEL[c.role]}</td>
                      <td className="p-2 tabular text-ink">{c.count}</td>
                      <td className="p-2 tabular text-ink-muted">{c.last_seen}</td>
                      <td className="flex justify-end gap-1 p-2">
                        <Button variant="primary" disabled={busy} data-testid="approve"
                          onClick={() => decide({ role: c.role, text: c.text, action: "approve" })}>
                          승인
                        </Button>
                        <Button variant="ghost" disabled={busy} data-testid="reject"
                          onClick={() => decide({ role: c.role, text: c.text, action: "reject" })}>
                          거절
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          <Panel title={`승인한 단어 ${shown.approved.length}개`} labelId="approved-title" provenance={{ precost: "real" }}>
            {shown.approved.length === 0 ? (
              <EmptyState title="아직 승인한 단어가 없습니다" />
            ) : (
              <ul className="flex flex-col divide-y divide-line" data-testid="approved-words">
                {shown.approved.map((w) => (
                  <li key={`${w.role}:${w.text}`} className="flex items-center gap-3 p-2 text-dense">
                    <span className="font-semibold text-ink">{w.text}</span>
                    <span className="text-label text-ink-muted">{ROLE_LABEL[w.role]}</span>
                    <Button variant="ghost" className="ml-auto" disabled={busy}
                      onClick={() => decide({ role: w.role, text: w.text, action: "remove" })}>
                      빼기
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <p className="px-2 pt-2 text-label text-ink-muted">
              승인한 단어는 작업 폴더에 저장됩니다. 도메인 팀이 룰을 검토할 때 data/rules/price_sheet.yaml로 옮깁니다.
            </p>
          </Panel>
        </>
      )}
    </main>
  );
}
