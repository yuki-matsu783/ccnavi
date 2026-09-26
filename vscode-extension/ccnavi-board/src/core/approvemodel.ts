/**
 * 承認の JSON の形（実行ファイルとの契約）と読み取り。README「承認の JSON」。
 *
 * `--approve --preview --json` が承認待ちの一覧を見せ、`--approve --yes <識別子,…> --json` が承認する。
 * 拡張は一覧の本文（`text`）をそのまま並べ、承認するときは見せた識別子をそのまま返す。
 * 承認の対象を自分で組み直したり、提案を読んだりはしない。
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
  /** チケットで編集対象としているが、書き込めない場所（親の範囲・種類の上限を超えた項）。承認は止めない。無ければ空 */
  readonly overflow: readonly string[];
}

export interface ApproveRejected {
  readonly ticket: string;
  readonly problems: readonly string[];
}

export interface ApprovePreview {
  readonly version: number;
  readonly root: string;
  readonly generated_at: string;
  /** `--approve` が承認する対象。空なら承認待ちが無い */
  readonly batch: readonly ApproveBatchEntry[];
  /** 承認画面の本文そのまま */
  readonly text: string;
  /** 承認画面の本文と承認済みチケットに写る中身の指紋（SHA-256、16 進）。中身は見ずに、承認するときに `--digest` で返す */
  readonly digest: string;
  /** 承認の対象にしない提案と、その理由 */
  readonly rejected: readonly ApproveRejected[];
  /** 読めない提案や承認済みチケットの説明 */
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
  /** 見せた指紋（渡した値）と今の指紋 */
  readonly digest: { readonly expected: string; readonly current: string };
}

/**
 * `--approve --yes` の答えを、呼ぶ側が読む形にしたもの。`partial`（途中で止まった）は
 * 文面にしてから `error` に入るので、ここには出てこない（`ccnavi.ts` の `runApproveYes`）。
 * 承認のオーバーレイの遷移（`approval-machine.ts`）が入力として受けるので、契約の側に置く
 */
export type ApproveOutcome =
  | { readonly ok: true; readonly value: ApproveResult }
  | { readonly ok: false; readonly mismatch: ApproveMismatch }
  | { readonly ok: false; readonly error: string };

export type PreviewParse =
  | { readonly ok: true; readonly value: ApprovePreview }
  | { readonly ok: false; readonly error: string };

/**
 * 途中で止まった承認（README「承認の JSON」の `partial`）。置いたものは戻らないので、
 * どこまで置いたかをそのまま受け取って人に伝える。
 */
export interface ApprovePartial {
  /** 承認済みチケットに入ったぶん（新規は置いた、改版は書き換えた） */
  readonly placed: readonly string[];
  /** 止まったところの識別子 */
  readonly ticket: string;
  /** 止まった理由（実行ファイルの文面そのまま） */
  readonly reason: string;
  /** 止まるまでに出た行（マーカーを消した、改版した）。端末なら標準出力に出ていたぶん */
  readonly lines: readonly string[];
}

/**
 * 途中で止まったことを人に伝える文。何が残っているかを言い切る。
 * `placed` に止まった識別子自身が入るのは、書けたあとの後始末（マーカーを置く）で
 * 落ちたとき。「i0001 で止まった…i0001 は入っている」と読めてしまうので、そこだけ言い方を変える。
 */
export function partialMessage(partial: ApprovePartial): string {
  const { placed, ticket, reason, lines } = partial;
  const where = ticket === "" ? "" : placed.includes(ticket) ? `${ticket} の後始末で` : `${ticket} で`;
  const what =
    placed.length === 0
      ? "承認済みになったチケットはありません"
      : `${placed.join(", ")} の ${placed.length} 件は承認済みチケットに入っています。` +
        "コミットと push は送っていません（送るのは承認できたときだけです）。チケット管理画面を更新して確かめてください";
  const done = lines.length === 0 ? "" : `\n${lines.join("\n")}`;
  return `ccnavi --approve --yes が${where === "" ? "" : ` ${where}`}止まりました: ${reason}。${what}${done}`;
}

export type ResultParse =
  | { readonly ok: true; readonly value: ApproveResult }
  | { readonly ok: false; readonly mismatch: ApproveMismatch }
  | { readonly ok: false; readonly partial: ApprovePartial }
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
      digest: str(raw.digest),
      rejected: list(raw.rejected)
        .filter(isRecord)
        .map((r) => ({ ticket: str(r.ticket), problems: list(r.problems).map(str) })),
      problems: list(raw.problems).map(str),
    },
  };
}

/** `--yes` の答え。承認できたか、一覧が変わっていたか、読めなかったか */
export function parseApproveResult(text: string): ResultParse {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  if (isRecord(raw.mismatch)) {
    const digest: Record<string, unknown> = isRecord(raw.mismatch.digest) ? raw.mismatch.digest : {};
    return {
      ok: false,
      mismatch: {
        expected: list(raw.mismatch.expected).map(str),
        current: list(raw.mismatch.current).map(str),
        digest: { expected: str(digest.expected), current: str(digest.current) },
      },
    };
  }
  // 途中で止まった。置いたものは戻らないので、mismatch と同じく成功にはしない。
  if (isRecord(raw.partial)) {
    return {
      ok: false,
      partial: {
        placed: list(raw.partial.placed).map(str),
        ticket: str(raw.partial.ticket),
        reason: str(raw.partial.reason),
        lines: list(raw.partial.lines).map(str),
      },
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
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== APPROVE_VERSION) {
    return {
      ok: false,
      error: `承認の JSON の版が違います（拡張は ${APPROVE_VERSION}、実行ファイルは ${String(raw.version)}）`,
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
    overflow: list(raw.overflow).map(str),
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
