/**
 * Pure helpers for the price-sheet column assigner: which columns to show, which rows
 * still belong to the header, and whether the picked roles are enough to read.
 */
import type { ColumnRole, SheetTable } from "@/lib/rfq/contract";

export type Roles = (ColumnRole | "")[];

/**
 * A column as shown: one or two columns of the table, and the one whose cells hold the values.
 * ``extra`` are hidden columns beside it whose only text is a title above the header.
 */
export type Shown = { cols: number[]; data: number; extra: number[] };

export function rolesOf(columns: SheetTable["columns"], width: number): Roles {
  const roles: Roles = Array.from({ length: width }, () => "");
  for (const [role, index] of Object.entries(columns)) {
    if (index !== undefined && index < width) roles[index] = role as ColumnRole;
  }
  return roles;
}

/**
 * The columns worth showing. A table read from a PDF often has empty columns, and a header cell
 * one column away from the values under it ("Description" over nothing, the names in the next
 * column): empty columns are left out, and such a header is shown over its values as one column.
 *
 * ``kept`` are columns the AI or a saved layout already assigned a role to: they stay visible
 * even when every data cell is empty and the label sits only on the row above (price / total).
 */
export function shownColumns(
  rows: string[][],
  header: number,
  end: number,
  width: number,
  kept: ReadonlySet<number> = new Set(),
): Shown[] {
  const text = (r: number, i: number) => (rows[r]?.[i] ?? "").trim() !== "";
  const head = (i: number) => text(header, i);
  const value = (i: number) => rows.some((_, r) => r >= end && text(r, i));
  // Titles above the header span the table; a column used only by them is not a column. Rows of
  // several cells with numbers above it are items (the header was guessed too low): they count.
  const items = (r: number) => rows[r].filter((c) => c.trim()).length >= 2 && rows[r].some((c) => /\d/.test(c));
  const used = (i: number) =>
    kept.has(i) || rows.some((_, r) => (r >= header || items(r)) && text(r, i));
  const shown: Shown[] = [];
  const leading: number[] = [];
  for (let i = 0; i < width; i++) {
    if (!used(i)) {
      (shown.length ? shown[shown.length - 1].extra : leading).push(i);
      continue;
    }
    if (head(i) && !value(i) && !kept.has(i)) {
      let j = i + 1;
      while (j < width && !used(j)) j++;
      if (j < width && !head(j) && value(j)) {
        shown.push({ cols: [i, j], data: j, extra: [] });
        i = j;
        continue;
      }
    }
    shown.push({ cols: [i], data: i, extra: [] });
  }
  shown[0]?.extra.unshift(...leading);
  return shown;
}

/**
 * Rows under the header that still belong to it, and how. A header cell may wrap onto the rows
 * below it ("ABC, inclusive of all" / "applicable taxes"): such a row, without digits and with
 * text only under header cells, is joined into the header ("wrap"), and so is the header printed
 * again. A row of sub-headings under a merged heading ("Total Bid Price" over "Unit Price | Total
 * Amount") stays a row of its own, shown as header ("sub"). A row with text in its first column
 * only is a group heading ("General equipment"), not part of the header.
 */
export function headerRows(rows: string[][], header: number): ("wrap" | "sub")[] {
  const first = rows[header].findIndex((c) => c.trim());
  const found: ("wrap" | "sub")[] = [];
  for (let r = header + 1; r < rows.length && r <= header + 3; r++) {
    const filled = rows[r].map((c, i) => [c.trim(), i] as const).filter(([c]) => c);
    const heading = filled.length === 1 && filled[0][1] <= Math.max(first, 0);
    if (filled.length === 0 || heading || filled.some(([c]) => /\d/.test(c))) break;
    const under = filled.every(([, i]) => (rows[header][i] ?? "").trim());
    if (under) found.push("wrap");
    else if (filled.length >= 2) found.push("sub");
    else break;
  }
  return found;
}

/** Header lines of one column joined; a line that repeats or extends the text so far replaces it. */
export function joinHeader(parts: string[]): string {
  return parts.reduce((acc, part) => {
    const text = part.trim();
    if (!text) return acc;
    if (!acc || text.startsWith(acc)) return text;
    return acc.includes(text) ? acc : `${acc} ${text}`;
  }, "");
}

export function problem(roles: Roles): string | null {
  if (!roles.includes("quantity")) return "수량 열을 지정하세요.";
  if (!roles.includes("description") && !roles.includes("part")) return "품명(설명) 열이나 품번 열을 지정하세요.";
  return null;
}

/** Max length of any row in the grid (ragged tables). */
export function gridWidth(rows: string[][]): number {
  return rows.length === 0 ? 0 : Math.max(...rows.map((r) => r.length));
}
