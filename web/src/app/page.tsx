"use client";
/**
 * Start screen (TASKS S010, SPEC §14, US7): the RFQ packages this account may open, and the upload
 * for accounts that may upload (docs/auth-rbac.md §6).
 */
import { RfqList } from "@/components/home/rfq-list";
import { UploadCard } from "@/components/home/upload-card";
import { AccountBar } from "@/components/shell/account-bar";
import { IconLock } from "@/components/ui/icons";
import { EngineBadge } from "@/components/ui/primitives";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";

const PRINCIPLES = [
  { title: "원문은 그대로", body: "원본을 고치지 않고 보관합니다. 모든 판단은 파일과 원문 위치로 돌아갑니다." },
  { title: "인용은 문장 단위", body: "결과마다 어느 파일 몇 페이지 몇 번째 문장인지 붙이고, 확인되지 않은 인용은 따로 표시합니다." },
  { title: "범위는 계정대로", body: "내 부서가 주관하거나 참조하는 조항이 열리고, 나머지는 번호와 자물쇠만 남습니다." },
];

export default function StartPage() {
  const viewer = useViewer();
  const { engines } = useMeta();

  return (
    <div className="mx-auto flex min-h-dvh max-w-288 flex-col px-4 md:px-8">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-3">
        <p data-particle-target="text" className="font-serif text-heading font-semibold text-ink">
          AutoRFx
        </p>
        {engines && <EngineBadge engines={engines} />}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <AccountBar />
        </div>
      </header>
      <main className="grid flex-1 content-center gap-12 py-12 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
        <section aria-labelledby="hero-title" className="flex min-w-0 flex-col justify-center gap-6">
          <h1
            id="hero-title"
            data-particle-target="text"
            className="font-serif text-ink"
            style={{ fontSize: "calc(clamp(28px, 5vw, 40px) + var(--bump))", lineHeight: 1.15 }}
          >
            여러 파일로 온 RFQ를,
            <br />
            <span className="italic">부서별 판단과 원문 근거</span>로.
          </h1>
          <p data-particle-target="text" data-particle-tone="muted" className="max-w-144 text-body text-ink-muted">
            요청서, 사양서, 물량표를 한 패키지로 읽어 조항을 주관·참조 부서로 나누고, 요건 강도와 독소조항을 표시하고, 파일
            사이에 어긋나는 값을 찾습니다. 모든 결과는 원문 문장에 연결됩니다.
          </p>
          <ol className="grid gap-4 border-t border-line pt-6 md:grid-cols-3 lg:grid-cols-1">
            {PRINCIPLES.map((p, i) => (
              <li key={p.title} className="flex gap-3">
                <span className="shrink-0 whitespace-nowrap font-serif text-heading text-ink-muted tabular">0{i + 1}</span>
                <span className="flex min-w-0 flex-col">
                  <span className="text-dense font-semibold text-ink">{p.title}</span>
                  <span className="text-dense text-ink-muted">{p.body}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>
        <div className="flex min-w-0 flex-col justify-center gap-6">
          {viewer.can_upload ? (
            <section aria-labelledby="upload-title">
              <UploadCard />
            </section>
          ) : (
            <p className="flex items-center gap-2 text-dense text-ink-muted" data-testid="no-upload">
              <IconLock size={14} className="shrink-0" />
              이 계정은 RFQ를 올릴 수 없습니다. 올라온 RFQ에서 내 부서의 조항을 볼 수 있습니다.
            </p>
          )}
          <RfqList />
        </div>
      </main>
    </div>
  );
}
