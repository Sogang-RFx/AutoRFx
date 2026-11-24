/** Honest translation wording (TRANS-03): name the engine as it is, say why a clause has none. */

export function translationLabel(id: string): string {
  if (id === "none") return "번역 없음";
  return id.replace(/^openai:/, "");
}

export function noTranslationMessage(lang: string, engineId: string): string {
  if (engineId === "none") return "번역 없음. 번역 제공자가 연결되지 않아 원문만 제공합니다.";
  // A clause labelled Korean may still be mostly English, and the response does not say which one it is.
  if (lang === "ko") return "원문이 국문이거나 번역을 제공하지 않는 조항입니다. 원문을 확인하세요.";
  return "이 조항은 번역을 제공하지 않습니다. 원문을 확인하세요.";
}
