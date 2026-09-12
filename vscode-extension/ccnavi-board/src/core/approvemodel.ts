/**
 * 承認の JSON の形（実行ファイルとの契約）と読み取り。README「承認の JSON」。
 *
 * `--approve --preview --json` が束を見せ、`--approve --yes <識別子,…> --json` が承認する。
 * 拡張は束の本文（`text`）をそのまま並べ、承認するときは見せた識別子をそのまま返す。
 * 束を自分で組み直したり、提案を読んだりはしない。
 */

export const APPROVE_VERSION = 1;

export interface ApproveBatchEntry {
  readonly ticket: string;
  readonly title: string;
  /** 子なら親の識別子。親なら null */
  readonly parent: string | null;
  readonly phase: number | null;
  /** 親の改版（計画の変更）か */
  readonly revision: boolean;
  readonly tree: string;
  readonly path: string;
}

export interface ApproveRejected {
  readonly ticket: string;
  readonly problems: readonly string[];
}

export interface ApprovePreview {
  readonly version: number;
  readonly root: string;
  readonly generated_at: string;
  /** `--approve` が承認する束。空なら承認待ちが無い */
  readonly batch: readonly ApproveBatchEntry[];
  /** 承認画面の本文そのまま */
  readonly text: string;
  /** 承認の対象にしない提案と、その理由 */
  readonly rejected: readonly ApproveRejected[];
  /** 読めない提案や写しの説明 */
  readonly problems: readonly string[];
}

export interface ApproveResult {
  readonly version: number;
  readonly approved: readonly string[];
  readonly copies: readonly string[];
  /** 端末なら標準出力に出ていた行 */
  readonly lines: readonly string[];
  /** Claude Code に渡す文。hook が次のプロンプトで渡す文と同じ */
  readonly prompt: string;
}

export interface ApproveMismatch {
  readonly expected: readonly string[];
  readonly current: readonly string[];
}

export type PreviewParse =
  | { readonly ok: true; readonly value: ApprovePreview }
  | { readonly ok: false; readonly error: string };

export type ResultParse =
  | { readonly ok: true; readonly value: ApproveResult }
  | { readonly ok: false; readonly mismatch: ApproveMismatch }
  | { readonly ok: false; readonly error: string };

export function parseApprovePreview(text: string): PreviewParse {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  return {
    ok: true,
    value: {
      version: top.version,
      root: str(raw.root),
      generated_at: str(raw.generated_at),
      batch: list(raw.batch).filter(isRecord).map(entry),
      text: str(raw.text),
      rejected: list(raw.rejected)
        .filter(isRecord)
        .map((r) => ({ ticket: str(r.ticket), problems: list(r.problems).map(str) })),
      problems: list(raw.problems).map(str),
    },
  };
}

/** `--yes` の答え。承認できたか、束が変わっていたか、読めなかったか */
export function parseApproveResult(text: string): ResultParse {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  if (isRecord(raw.mismatch)) {
    return {
      ok: false,
      mismatch: { expected: list(raw.mismatch.expected).map(str), current: list(raw.mismatch.current).map(str) },
    };
  }
  return {
    ok: true,
    value: {
      version: top.version,
      approved: list(raw.approved).map(str),
      copies: list(raw.copies).map(str),
      lines: list(raw.lines).map(str),
      prompt: str(raw.prompt),
    },
  };
}

type Top =
  | { readonly ok: true; readonly raw: Record<string, unknown>; readonly version: number }
  | { readonly ok: false; readonly error: string };

function parseTop(text: string): Top {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めない: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではない" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== APPROVE_VERSION) {
    return {
      ok: false,
      error: `承認の JSON の版が違う（拡張は ${APPROVE_VERSION}、実行ファイルは ${String(raw.version)}）`,
    };
  }
  return { ok: true, raw, version };
}

function entry(raw: Record<string, unknown>): ApproveBatchEntry {
  return {
    ticket: str(raw.ticket),
    title: str(raw.title),
    parent: typeof raw.parent === "string" && raw.parent !== "" ? raw.parent : null,
    phase: typeof raw.phase === "number" && Number.isInteger(raw.phase) ? raw.phase : null,
    revision: raw.revision === true,
    tree: str(raw.tree),
    path: str(raw.path),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}
