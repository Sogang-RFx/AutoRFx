/** How one row of the RFQ list reads: state, where it leads, and what can be done when it has no results. */
import type { RfqListItem } from "./contract";

export type RfqStatusView = { label: string; href: string | null; note: string | null };

const again = (canUpload: boolean) =>
  canUpload ? "같은 파일을 다시 올리면 새로 분석합니다." : "업로드할 수 있는 계정이 같은 파일을 다시 올려야 합니다.";

export function rfqStatusView(item: RfqListItem, canUpload: boolean): RfqStatusView {
  const base = `/rfq/${encodeURIComponent(item.id)}`;
  switch (item.status) {
    case "done":
      return { label: "분석 완료", href: `${base}/clauses`, note: null };
    case "running":
      return { label: "분석 중", href: `${base}/processing`, note: null };
    case "failed":
      return {
        label: "분석 멈춤",
        href: `${base}/processing`,
        note: `분석이 중간에 멈춰 결과가 없습니다. ${again(canUpload)}`,
      };
    case "stale":
      return {
        label: "오래된 결과",
        href: null,
        note: `규칙이나 처리 방식이 바뀌어 저장된 결과를 보여주지 않습니다. ${again(canUpload)}`,
      };
  }
}
