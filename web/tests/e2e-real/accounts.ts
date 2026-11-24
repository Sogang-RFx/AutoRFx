/**
 * Accounts of the e2e suite, read from the file named by RFQ_E2E_ACCOUNTS: the ACCOUNTS.local.md
 * written by `autorfx users seed`, or JSON. No username or password is written in this repository.
 */
import { existsSync, readFileSync } from "node:fs";

export type Accounts = Record<string, string>;

const USERNAME = /^[a-z0-9][a-z0-9_-]{2,31}$/;
const bare = (cell: string) => cell.trim().replace(/^[`*]+|[`*]+$/g, "");

function fromJson(text: string): Accounts | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  const nested = (data as { accounts?: unknown } | null)?.accounts ?? data;
  if (Array.isArray(nested)) {
    return Object.fromEntries(
      nested.map((a: { username: string; password: string }) => [a.username, a.password]),
    );
  }
  return nested as Accounts;
}

/** A Markdown table, its columns found by their headings. */
function fromTable(lines: string[]): Accounts {
  const out: Accounts = {};
  let columns: { user: number; password: number } | null = null;
  for (const line of lines) {
    if (!line.trim().startsWith("|")) {
      columns = null;
      continue;
    }
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map(bare);
    if (!columns) {
      const user = cells.findIndex((c) => /아이디|username|계정 id|^id$/i.test(c));
      const password = cells.findIndex((c) => /비밀번호|password/i.test(c));
      if (user >= 0 && password >= 0) columns = { user, password };
    } else if (USERNAME.test(cells[columns.user] ?? "") && cells[columns.password]) {
      out[cells[columns.user]] = cells[columns.password];
    }
  }
  return out;
}

/** "- admin: password" lines. */
function fromList(lines: string[]): Accounts {
  const pairs = lines.map((l) => /^[-*\s]*`?([a-z0-9][a-z0-9_-]{2,31})`?\s*[:=]\s+(\S+)\s*$/.exec(l));
  return Object.fromEntries(pairs.filter((m) => m !== null).map((m) => [m[1], bare(m[2])]));
}

export function parseAccounts(text: string): Accounts {
  const json = fromJson(text);
  if (json) return json;
  const lines = text.split(/\r?\n/);
  const table = fromTable(lines);
  return Object.keys(table).length > 0 ? table : fromList(lines);
}

/** Empty when the variable is not set, so the suite is skipped; a path that does not exist is an error. */
export function loadAccounts(file: string | undefined): Accounts {
  if (!file?.trim()) return {};
  if (!existsSync(file)) throw new Error(`RFQ_E2E_ACCOUNTS의 파일이 없습니다: ${file}`);
  return parseAccounts(readFileSync(file, "utf8"));
}
