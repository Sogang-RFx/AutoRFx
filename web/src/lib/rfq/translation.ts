/** Honest translation wording (TRANS-03): name the engine as it is, say why a clause has none. */

import type { TranslationStatus } from "@/lib/rfq/contract";

export function translationLabel(id: string): string {
  if (id === "none") return "번역 없음";
  return id.replace(/^openai:/, "");
}

const NO_PROVIDER = "번역 없음. 번역 제공자가 연결되지 않아 원문만 제공합니다.";

/** What the analysis recorded comes first; the engine is only the server's current setting. */
const BY_STATUS: Partial<Record<TranslationStatus, string>> = {
  no_provider: NO_PROVIDER,
  source_ko: "원문이 국문입니다.",
  boilerplate: "상용구 조항이라 번역하지 않습니다. 원문을 확인하세요.",
  withheld: "번역에 원문에 없는 숫자가 있어 보류했습니다. 원문을 확인하세요.",
  failed: "번역하지 못했습니다(호출 실패 또는 빈 응답). 원문을 확인하세요.",
};

export function noTranslationMessage(lang: string, engineId: string, status?: TranslationStatus): string {
  const recorded = status && BY_STATUS[status];
  if (recorded) return recorded;
  if (engineId === "none") return NO_PROVIDER;
  // Older results: a clause labelled Korean may still be mostly English, and nothing says which one it is.
  if (lang === "ko") return "원문이 국문이거나 번역을 제공하지 않는 조항입니다. 원문을 확인하세요.";
  return "이 조항은 번역을 제공하지 않습니다. 원문을 확인하세요.";
}
