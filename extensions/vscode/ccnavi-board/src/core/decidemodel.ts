/**
 * 残った指摘を決める JSON の形（実行ファイルとの契約）と読み取り。docs/requirements/json-shapes.md「残った指摘の JSON」。
 *
 * `ccnavi-review.sh decide <N> --preview` が残った指摘とダイジェストを見せ、
 * `ccnavi-user-decide.sh decide <N> --choices <JSON> --digest <ダイジェスト>` がユーザの選んだ行き先を置く。
 * 拡張は見せられた指摘をそのまま並べ、選んだ行き先をキーごとに返すだけ
 * （マージリクエストを読むのは sh、判断するのは実行ファイル）。
 */

/** 版 2: ダイジェストは指摘ごとの版（見せたあとに追加された指摘は止めない）。版 1 の JSON は読まない */
export const DECIDE_VERSION = 2;

/** 行き先。対応しない（受け入れて進む）・このフェーズで直す（続きの子）・issue に回す */
export type DecideChoice = "keep" | "fix" | "issue";

export const DECIDE_CHOICES: readonly DecideChoice[] = ["keep", "fix", "issue"];

export const DECIDE_LABELS: Readonly<Record<DecideChoice, string>> = {
  keep: "対応しない",
  fix: "このフェーズで直す",
  issue: "issue に回す",
};

export interface DecideThread {
  /** 選択を結ぶキー（URL か、無ければスレッドの id） */
  readonly key: string;
  readonly url: string;
  readonly path: string;
  readonly line: number;
  readonly body: string;
  /** 指摘の版（キーと最初のコメントの本文から決まる）。画面は見せるだけで、解釈しない */
  readonly version: string;
  /** 最後のコメント（再提起を見分ける）。取れなければ空 */
  readonly last_comment: { readonly id: string; readonly at: string; readonly author: string };
}

export interface DecidePreview {
  readonly version: number;
  readonly parent: string;
  readonly phase: number;
  readonly mr: { readonly number: number; readonly url: string };
  /** issue に回せるか。常に true（JSON の欄は互換のために残す。選ぶ・選ばないの判定には使わない） */
  readonly can_issue: boolean;
  readonly threads: readonly DecideThread[];
  /** 見せた指摘のダイジェスト（不透明な文字列）。選択を置くときに `--digest` で返す */
  readonly digest: string;
}

export interface DecideResult {
  readonly parent: string;
  readonly phase: number;
  /** レビュー済みになったか（直す指摘も見せたあとに増えた指摘も無ければ真） */
  readonly reviewed: boolean;
  /** 見せたあとに追加された指摘（今回の判断の対象外。未対応として残る）のキー */
  readonly pending_extra: readonly string[];
  /** 起こした続きの子の識別子。起こしていなければ空 */
  readonly followup: string;
  /** Claude Code に渡す文 */
  readonly prompt: string;
  /** issue に回した分で作った issue の URL。無ければ空 */
  readonly issue_url: string;
  /** 置いたあとの投稿（issue・コメント）で起きたこと。置いたことは戻らない */
  readonly warning: string;
}

export type DecidePreviewParse =
  | { readonly ok: true; readonly value: DecidePreview }
  | { readonly ok: false; readonly error: string };

/** 選択を置いた答え。置けた、見せた指摘と今の指摘が違った、読めなかった */
export type DecideOutcome =
  | { readonly ok: true; readonly value: DecideResult }
  | { readonly ok: false; readonly mismatch: true }
  | { readonly ok: false; readonly error: string };

/**
 * 読み取りの検証が照らす、頼んだもの。`mr` はフェーズ行の依頼が持つ MR の番号（分かるときだけ。分からなければ null で、番号の食い違いは見ない）
 */
export interface DecideExpect {
  readonly parent: string;
  readonly phase: number;
  readonly mr: number | null;
}

/**
 * `ccnavi-review.sh decide <N> --preview` の標準出力を読む。**厳しく読む**。次のどれかなら、件数を出さずに
 * 「指摘を読み取れなかった」として扱う（呼び手は確定のボタンを出さない）。「未解決は 0 件」と読み間違えると、
 * 指摘が残っているフェーズを「レビュー済みにする」と見せてしまうため。
 *
 * - JSON でない、最上位がマップでない、`version` が想定外
 * - `parent` が文字列でない、または頼んだものと違う
 * - `phase` が整数でない、または頼んだものと違う
 * - `mr.number` が整数でない、または頼んだ MR の番号（分かるとき）と違う
 * - `digest` が無い、文字列でない、`v2.` で始まらない
 * - `threads` が無い、または配列でない
 * - スレッドの要素がマップでない、`key` が空・文字列でない・重複している、`body`・`url`・`path` が文字列でない、`line` が整数でない
 *   （不正な要素は破棄せずに、全体をエラーにする）
 *
 * URL が http(s) でないことはエラーにしない（画面がリンクにしないだけ）。
 * `expect` を渡さなければ、頼んだものとの照合だけを省く（型の検査はする）
 */
export function parseDecidePreview(text: string, expect?: DecideExpect): DecidePreviewParse {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  const fail = (reason: string): DecidePreviewParse => ({ ok: false, error: reason });
  if (typeof raw.parent !== "string") {
    return fail("parent が文字列ではありません");
  }
  if (expect !== undefined && raw.parent !== expect.parent) {
    return fail(`parent が頼んだもの（${expect.parent}）と違います（${raw.parent}）`);
  }
  if (!isInt(raw.phase)) {
    return fail("phase が整数ではありません");
  }
  if (expect !== undefined && raw.phase !== expect.phase) {
    return fail(`phase が頼んだもの（${expect.phase}）と違います（${raw.phase}）`);
  }
  const mr = isRecord(raw.mr) ? raw.mr : undefined;
  if (mr === undefined || !isInt(mr.number)) {
    return fail("mr.number が整数ではありません");
  }
  if (expect !== undefined && expect.mr !== null && mr.number !== expect.mr) {
    return fail(`mr.number が頼んだ依頼のマージリクエスト（#${expect.mr}）と違います（#${mr.number}）`);
  }
  if (typeof raw.digest !== "string" || !raw.digest.startsWith("v2.")) {
    return fail("digest が無いか、版 2（v2. で始まる文字列）ではありません");
  }
  if (!Array.isArray(raw.threads)) {
    return fail("threads が配列ではありません");
  }
  const threads: DecideThread[] = [];
  const seen = new Set<string>();
  for (const [i, item] of raw.threads.entries()) {
    const read = thread(item, i);
    if (typeof read === "string") {
      return fail(read);
    }
    if (seen.has(read.key)) {
      return fail(`threads[${i}] の key が重複しています（${read.key}）`);
    }
    seen.add(read.key);
    threads.push(read);
  }
  return {
    ok: true,
    value: {
      version: top.version,
      parent: raw.parent,
      phase: raw.phase,
      mr: { number: mr.number, url: str(mr.url) },
      can_issue: raw.can_issue === true,
      threads,
      digest: raw.digest,
    },
  };
}

/**
 * 選択を置いた答え。sh は実行ファイルの答えに issue の URL と投稿の警告を足して返す。
 * 失敗の答え（`mismatch`）は終了コードが 0 でなくても JSON で来るので、呼び手は先にこれを読む
 */
export function parseDecideResult(text: string): DecideOutcome {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  if (raw.mismatch === true) {
    return { ok: false, mismatch: true };
  }
  if (raw.ok !== true) {
    return { ok: false, error: "反映できたか分かりません（出力の ok が true ではありません）" };
  }
  return {
    ok: true,
    value: {
      parent: str(raw.parent),
      phase: int(raw.phase),
      reviewed: raw.reviewed === true,
      pending_extra: list(raw.pending_extra).filter((k): k is string => typeof k === "string"),
      followup: str(raw.followup),
      prompt: str(raw.prompt),
      issue_url: str(raw.issue_url),
      warning: str(raw.warning),
    },
  };
}

/**
 * 選んだ行き先が、見せた指摘の全部に 1 つずつ付いているか。付いていなければ理由を返す。
 * 押せるかどうかを画面が決め、送る前に遷移の側でも同じものを見る（古い画面から届いた選択を通さない）
 */
export function choicesProblem(
  preview: DecidePreview,
  choices: Readonly<Record<string, string>>,
): string | undefined {
  const keys = preview.threads.map((t) => t.key);
  const given = Object.keys(choices);
  if (given.length !== keys.length || !keys.every((k) => k in choices)) {
    return "対応方針を選んでいない指摘があります";
  }
  for (const key of keys) {
    const choice = choices[key];
    if (!DECIDE_CHOICES.includes(choice as DecideChoice)) {
      return `不明な対応方針です: ${String(choice)}`;
    }
  }
  return undefined;
}

type Top =
  | { readonly ok: true; readonly raw: Record<string, unknown>; readonly version: number }
  | { readonly ok: false; readonly error: string };

function parseTop(text: string): Top {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== DECIDE_VERSION) {
    return {
      ok: false,
      error: `未解決の指摘の JSON の版が違います（拡張は ${DECIDE_VERSION}、実行ファイルは ${String(raw.version)}）`,
    };
  }
  return { ok: true, raw, version };
}

/** スレッド 1 件を読む。読めなければ、何番目がなぜ不正かの文を返す */
function thread(item: unknown, index: number): DecideThread | string {
  const at = `threads[${index}]`;
  if (!isRecord(item)) {
    return `${at} がマップではありません`;
  }
  if (typeof item.key !== "string" || item.key === "") {
    return `${at} の key が空か、文字列ではありません`;
  }
  for (const name of ["body", "url", "path"] as const) {
    if (typeof item[name] !== "string") {
      return `${at} の ${name} が文字列ではありません`;
    }
  }
  if (!isInt(item.line)) {
    return `${at} の line が整数ではありません`;
  }
  const last: Record<string, unknown> = isRecord(item.last_comment) ? item.last_comment : {};
  return {
    key: item.key,
    url: item.url as string,
    path: item.path as string,
    line: item.line,
    body: item.body as string,
    version: str(item.version),
    last_comment: { id: str(last.id), at: str(last.at), author: str(last.author) },
  };
}

function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
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

function int(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) ? value : 0;
}
