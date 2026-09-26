/**
 * 残った指摘を決める JSON の形（実行ファイルとの契約）と読み取り。README「残った指摘の JSON」。
 *
 * `ccnavi-review.sh decide <N> --preview` が残った指摘と指紋を見せ、
 * `ccnavi-review.sh decide <N> --choices <JSON> --digest <指紋>` が人の選んだ行き先を置く。
 * 拡張は見せられた指摘をそのまま並べ、選んだ行き先を鍵ごとに返すだけ。指摘を自分で数え直したり、
 * マージリクエストを読んだりはしない（読むのは sh、判断するのは実行ファイル）。
 */

export const DECIDE_VERSION = 1;

/** 行き先。対応しない（受け入れて進む）・このフェーズで直す（続きの子）・issue に回す */
export type DecideChoice = "keep" | "fix" | "issue";

export const DECIDE_CHOICES: readonly DecideChoice[] = ["keep", "fix", "issue"];

export const DECIDE_LABELS: Readonly<Record<DecideChoice, string>> = {
  keep: "対応しない",
  fix: "このフェーズで直す",
  issue: "issue に回す",
};

export interface DecideThread {
  /** 選択を結ぶ鍵（URL か、無ければスレッドの id） */
  readonly key: string;
  readonly url: string;
  readonly path: string;
  readonly line: number;
  readonly body: string;
}

export interface DecidePreview {
  readonly version: number;
  readonly parent: string;
  readonly phase: number;
  readonly mr: { readonly number: number; readonly url: string };
  /** issue に回せるか（フィードバック計画が承認されたあとだけ） */
  readonly can_issue: boolean;
  readonly threads: readonly DecideThread[];
  /** 見せた指摘の指紋。選択を置くときに `--digest` で返す */
  readonly digest: string;
}

export interface DecideResult {
  readonly parent: string;
  readonly phase: number;
  /** レビュー済みになったか（直す指摘が無ければ真） */
  readonly reviewed: boolean;
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

export function parseDecidePreview(text: string): DecidePreviewParse {
  const top = parseTop(text);
  if (!top.ok) {
    return top;
  }
  const raw = top.raw;
  const mr: Record<string, unknown> = isRecord(raw.mr) ? raw.mr : {};
  const digest = str(raw.digest);
  if (!/^[0-9a-f]{64}$/.test(digest)) {
    return { ok: false, error: "digest が読めません（64 桁の 16 進ではありません）" };
  }
  return {
    ok: true,
    value: {
      version: top.version,
      parent: str(raw.parent),
      phase: int(raw.phase),
      mr: { number: int(mr.number), url: str(mr.url) },
      can_issue: raw.can_issue === true,
      threads: list(raw.threads).filter(isRecord).map(thread),
      digest,
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
      return `知らない対応方針です: ${String(choice)}`;
    }
    if (choice === "issue" && !preview.can_issue) {
      return "issue に回せるのは、フィードバック計画が承認されたあとです";
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
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
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

function thread(raw: Record<string, unknown>): DecideThread {
  return { key: str(raw.key), url: str(raw.url), path: str(raw.path), line: int(raw.line), body: str(raw.body) };
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
