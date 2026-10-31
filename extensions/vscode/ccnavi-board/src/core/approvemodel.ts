/**
 * 承認の JSON の形（実行ファイルとの契約）と読み取り。docs/11_requirements/json-shapes.md「承認の JSON」。
 *
 * `--agree --preview --json` が承認先行の一覧を見せ、`user agree --yes <識別子,…> --json` が承認する。
 * 拡張は一覧の本文（`text`）をそのまま並べ、承認するときは見せた識別子をそのまま返す。
 */

export const APPROVE_VERSION = 1;

export interface ApproveBatchEntry {
  readonly ticket: string;
  readonly title: string;
  /** 子なら親の識別子。親なら null */
  readonly parent: string | null;
  readonly phase: number | null;
  /** 親の改版（計画・コピー・範囲の変更）か */
  readonly revision: boolean;
  readonly tree: string;
  readonly path: string;
  /** チケットで編集対象としているが、書き込めない場所（親の範囲・定義の上限を超えた項）。承認は止めない。無ければ空 */
  readonly overflow: readonly string[];
}

export interface ApproveRejected {
  readonly ticket: string;
  readonly problems: readonly string[];
}

/**
 * 計画の図のステップ 1 つ（`plans[].items[]`）。値は実行ファイルが組んだもの。図は印を付けて描くだけで、
 * 見る場所・延期の引き受け手・固定したかを自分で決めない
 */
export interface ApprovePlanItem {
  /** 番号（承認の preview では提案に書いてある番号のまま） */
  readonly number: number;
  /** 振り直す前の番号。振り直していなければ `number` と同じ */
  readonly from: number;
  /** 定義の名前 */
  readonly type: string;
  /** 定義のタイトル（親の `phases:` から） */
  readonly title: string;
  /** レビューが要るか（ステップの `review: true` は true）。定義が読めなければ null */
  readonly review: boolean | null;
  /** ステップの `deferred: true`（レビューを後ろのステップへ延ばす） */
  readonly deferred: boolean;
  /** 延期したレビューを引き受ける番号。延期していなければ null */
  readonly review_at: number | null;
  /** 子が承認された番号（固定した番号。改版で動かせない） */
  readonly locked: boolean;
}

/**
 * 計画を持つ親の、計画 1 つぶん（全体計画かフィードバック計画）の図の中身。`--agree --preview --json` の
 * `plans` と `--plan-order <親> --json` の `plans` は実行ファイルの同じ関数が組む（docs/11_requirements/json-shapes.md「承認の JSON」）。
 * 先行（`after`）は番号の文字列 → 直接の先行の番号のリスト
 */
export interface ApprovePlan {
  readonly ticket: string;
  /** `plan`（全体計画）か `feedback`（フィードバック計画） */
  readonly part: string;
  /** 親の提案とその子の提案をまとめたハッシュ（計画編集タブの保存で `--expect` に返す） */
  readonly source_sha: string;
  readonly items: readonly ApprovePlanItem[];
  /** 直接の先行（番号は `number`） */
  readonly after: Readonly<Record<string, readonly number[]>>;
  /** 提案に書いてある直接の先行 */
  readonly proposed: Readonly<Record<string, readonly number[]>>;
  /** 改版のとき、承認済みの先行関係から戻した直接の先行。改版でなければ null */
  readonly current: Readonly<Record<string, readonly number[]>> | null;
  /** 最後のステップの先行になっていないステップ（終端に届かないステップ） */
  readonly loose: readonly number[];
  /** 先行なしですぐ始まるステップ */
  readonly ready: readonly number[];
  /** 順序の検査の error（実行ファイルの文面そのまま） */
  readonly problems: readonly string[];
}

export interface ApprovePreview {
  readonly version: number;
  readonly root: string;
  readonly generated_at: string;
  /** `user agree` が承認する対象。空なら承認待ちが無い */
  readonly batch: readonly ApproveBatchEntry[];
  /** 承認画面の本文そのまま */
  readonly text: string;
  /** 提案の識別子の一覧と各提案ファイルの元のバイト列全体のダイジェスト（SHA-256、16 進）。中身は見ずに、承認するときに `--digest` で返す */
  readonly digest: string;
  /** 承認の対象にしない提案と、その理由 */
  readonly rejected: readonly ApproveRejected[];
  /** 読めない提案や承認済みチケットの説明 */
  readonly problems: readonly string[];
  /** 承認の対象に入った計画を持つ親の図の中身（計画ごとに 1 件）。欄が無ければ空で、画面は図を出さない */
  readonly plans: readonly ApprovePlan[];
}

/** 承認画面の要約。`batch` と `plans` にあるものだけから作る（範囲などは本文にしか無いので扱わない） */
export interface ApproveSummary {
  /** 承認するチケットの件数の行。一覧が空なら空文字 */
  readonly tickets: string;
  /** 計画ごとの行（親・全体計画／フィードバック計画の順は届いた順）。計画が無ければ空 */
  readonly plans: readonly string[];
}

const PART_LABELS: Readonly<Record<string, string>> = { plan: "全体計画", feedback: "フィードバック計画" };

export function summarize(preview: ApprovePreview): ApproveSummary {
  const batch = preview.batch;
  if (batch.length === 0) {
    return { tickets: "", plans: [] };
  }
  const parents = batch.filter((b) => b.parent === null).length;
  const revisions = batch.filter((b) => b.revision).length;
  const kinds = [`親 ${parents} 件`, `子 ${batch.length - parents} 件`, ...(revisions > 0 ? [`改版 ${revisions} 件`] : [])];
  const revised = new Set(batch.filter((b) => b.revision).map((b) => b.ticket));
  const plans = preview.plans.map((plan) => {
    // 番号順に並べるだけにする。計画は先行関係で並列のステップもあるため、矢印では書かない
    const names = [...plan.items].sort((a, b) => a.number - b.number).map((item) => item.title || item.type);
    const label = PART_LABELS[plan.part] ?? plan.part;
    return `${plan.ticket}${revised.has(plan.ticket) ? "（改版）" : " "}の${label} ${names.length} ステップ（${names.join("・")}）`;
  });
  return { tickets: `承認するチケット ${batch.length} 件（${kinds.join("・")}）`, plans };
}

/**
 * 一覧のうち、計画編集タブに未保存の変更がある親（識別子の順）。親は一覧の親（`parent` が null。改版も）と、
 * 図のある親。子の識別子は親のタブと取り違えない。**空でなければ承認を打たない**（承認の遷移のガードと、画面のボタン）
 */
export function unsavedParents(preview: ApprovePreview, unsaved: readonly string[]): string[] {
  const parents = new Set([...preview.batch.filter((b) => b.parent === null).map((b) => b.ticket), ...preview.plans.map((p) => p.ticket)]);
  return [...new Set(unsaved)].filter((ticket) => parents.has(ticket)).sort();
}

/** 未保存のタブがあるので承認できない理由 */
export function unsavedText(parents: readonly string[]): string {
  return `親 ${parents.join(", ")} の計画編集タブに未保存の変更があります。保存するか破棄してから承認してください`;
}

/**
 * タブで保存したあとに提案が変わった親（識別子の順）。タブが最後に保存したときの `source_sha`（`saved`）と、一覧の
 * 計画の `source_sha` が違う親。承認は止めない（図は今の提案をそのまま出している）
 */
export function changedSinceSave(preview: ApprovePreview, saved: Readonly<Record<string, string>>): string[] {
  const out = new Set<string>();
  for (const plan of preview.plans) {
    const sha = saved[plan.ticket];
    if (sha !== undefined && sha !== plan.source_sha) {
      out.add(plan.ticket);
    }
  }
  return [...out].sort();
}

export interface ApproveResult {
  readonly version: number;
  readonly approved: readonly string[];
  readonly copies: readonly string[];
  /**
   * この承認で書いた・消したパス（ワークスペースルートからの相対、`/` 区切り）。承認の push の sh に
   * `--path` で渡し、これだけをコミット・push させる。古い実行ファイルは返さない（空）
   */
  readonly written: readonly string[];
  /** ターミナルなら標準出力に出ていた行 */
  readonly lines: readonly string[];
  /** 承認した子のワークツリーの計画。`state` が `create` のものを拡張が承認の後に作る（`ccnavi-git.sh worktree add`） */
  readonly worktrees: readonly ApproveWorktree[];
  /**
   * Claude Code に渡す文（承認された識別子と後工程の進め方）。hook は承認を知らせないので、
   * 拡張がこの文を渡す
   */
  readonly prompt: string;
}

/** 承認した子のワークツリーの計画 1 本（実行ファイルの `worktrees`。状態は `create` `exists` `no-parent` `project`） */
export interface ApproveWorktree {
  readonly ticket: string;
  readonly parent: string;
  /** ワークスペースルートからの相対（`/` 区切り） */
  readonly path: string;
  readonly branch: string;
  /** 親のワークツリーが今いるブランチ。`create` のときだけ入る */
  readonly base: string;
  readonly state: string;
}

/** 作れなかった子のワークツリーと、その理由（作る sh の標準エラーの 1 行目など） */
export interface ChildWorktreeFailure {
  readonly worktree: ApproveWorktree;
  readonly reason: string;
}

/**
 * `ccnavi-git.sh` の失敗から理由を 1 行で取る。sh は結果を標準出力に出し標準エラーは空のことがあるので、
 * `fatal:` / `error:` の行を優先し、無ければ標準出力の 1 行目（ログの場所を含む）を返す。
 */
export function failureReason(stderr: string, stdout: string): string {
  const lines = `${stderr}\n${stdout}`.split("\n").map((l) => l.trim()).filter((l) => l !== "");
  const cause = lines.find((l) => /^(fatal|error):/.test(l));
  return cause ?? lines[0] ?? "";
}

/**
 * 作れなかった子のワークツリーを、名前と手で打つコマンドで知らせる文（1 本 1 行）。
 * 同名のブランチが既にあって失敗したときは `-b` の形が再び失敗するので、既存のブランチを出す形も添える。
 * `gitCommand` は `ccnavi-git.sh` を呼ぶ sh の前置き（`commands.ts` の `gitScriptCommand`）。
 */
export function childWorktreeFailureText(failed: readonly ChildWorktreeFailure[], gitCommand: string): string {
  return failed
    .map(({ worktree: w, reason }) =>
      `子チケット ${w.ticket} のワークツリー ${w.path} を作成できませんでした（${reason}）。` +
      `手で作るなら: ${gitCommand} worktree add ${w.path} -b ${w.branch} ${w.base}。` +
      `ブランチ ${w.branch} が既にあるなら: ${gitCommand} worktree add ${w.path} ${w.branch}`,
    )
    .join("\n");
}

export interface ApproveMismatch {
  readonly expected: readonly string[];
  readonly current: readonly string[];
  /** 見せたダイジェスト（渡した値）と今のダイジェスト */
  readonly digest: { readonly expected: string; readonly current: string };
}

/**
 * `user agree --yes` の答えを、呼ぶ側が読む形にしたもの。`partial`（途中で止まった）は、置けたぶんが
 * あればコミット・pushを送るため、文面にせず遷移の側へ渡す（`approval-machine.ts` の `answered`）。
 * 承認のオーバーレイの遷移が入力として受けるので、契約の側に置く
 */
export type ApproveOutcome =
  | { readonly ok: true; readonly value: ApproveResult }
  | { readonly ok: false; readonly mismatch: ApproveMismatch }
  | { readonly ok: false; readonly partial: ApprovePartial }
  | { readonly ok: false; readonly error: string };

export type PreviewParse =
  | { readonly ok: true; readonly value: ApprovePreview }
  | { readonly ok: false; readonly error: string };

/**
 * 途中で止まった承認（docs/11_requirements/json-shapes.md「承認の JSON」の `partial`）。置いたものは戻らないので、
 * どこまで置いたかをそのまま受け取ってユーザに伝える。
 */
export interface ApprovePartial {
  /** 承認済みチケットに入ったぶん（新規は置いた、改版は書き換えた） */
  readonly placed: readonly string[];
  /** 実際に書けた承認済みチケットのパス（`placed` の分） */
  readonly paths: readonly string[];
  /** この承認で書いた・消したパス（ワークスペースルートからの相対）。`ApproveResult.written` と同じ */
  readonly written: readonly string[];
  /** 止まったところの識別子 */
  readonly ticket: string;
  /** 止まった理由（実行ファイルの文面そのまま） */
  readonly reason: string;
  /** 止まるまでに出た行（マーカーを消した、改版した）。ターミナルなら標準出力に出ていたぶん */
  readonly lines: readonly string[];
}

/**
 * 途中で止まったことをユーザに伝える文。何が残っているかを言い切る。
 * `placed` に止まった識別子自身が入るのは、書けたあとの後始末（マーカーを置く）で
 * 失敗したとき。「i0001 で止まった…i0001 は入っている」と読めてしまうので、そこだけ言い方を変える。
 */
export function partialMessage(partial: ApprovePartial, carried = false): string {
  const { placed, paths, ticket, reason, lines } = partial;
  const where = ticket === "" ? "" : placed.includes(ticket) ? `${ticket} の後始末で` : `${ticket} で`;
  const written = paths.length === 0 ? placed.join(", ") : paths.join(", ");
  const failed = ticket === "" || placed.includes(ticket) ? "" : `書き込めませんでした: ${ticket}。`;
  const sent = carried
    ? "書けたぶんだけをコミット・push するコマンドをターミナルに送信しました。結果はターミナルで確認してください（push できていなければ未送信です）"
    : "承認済みチケットをコミット・push するコマンドは送信していません（push 用のスクリプトがありません）。未送信です";
  const what =
    placed.length === 0
      ? "承認済みになったチケットはありません"
      : `書けた承認済みチケット（${placed.length} 件）: ${written}。${failed}${sent}`;
  const done = lines.length === 0 ? "" : `\n${lines.join("\n")}`;
  return `ccnavi user agree --yes が${where === "" ? "" : ` ${where}`}止まりました: ${reason}。${what}${done}`;
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
      plans: readPlans(raw.plans),
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
        paths: list(raw.partial.paths).map(str),
        written: list(raw.partial.written).map(str),
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
      written: list(raw.written).map(str),
      lines: list(raw.lines).map(str),
      worktrees: list(raw.worktrees).filter(isRecord).map((w) => ({
        ticket: str(w.ticket),
        parent: str(w.parent),
        path: str(w.path),
        branch: str(w.branch),
        base: str(w.base),
        state: str(w.state),
      })),
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
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
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

/** 1 以上の整数か */
function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

/** 番号 → 番号のリスト。リストでない値と、数でない番号は落とす */
function numbersBy(value: unknown): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  if (!isRecord(value)) {
    return out;
  }
  for (const [key, numbers] of Object.entries(value)) {
    if (Array.isArray(numbers)) {
      out[key] = numbers.filter(isNumber);
    }
  }
  return out;
}

/**
 * `plans` の欄を読む。`--plan-order <親> --json` の答え（`planordermodel.ts`）も同じ形なので、同じ読み方を使う。
 * オブジェクトでないステップは落とし、番号の無いステップも落とす
 */
export function readPlans(value: unknown): ApprovePlan[] {
  return list(value).filter(isRecord).map(plan);
}

function plan(raw: Record<string, unknown>): ApprovePlan {
  return {
    ticket: str(raw.ticket),
    part: str(raw.part),
    source_sha: str(raw.source_sha),
    items: list(raw.items)
      .filter(isRecord)
      .filter((item) => isNumber(item.number))
      .map((item) => ({
        number: item.number as number,
        from: isNumber(item.from) ? item.from : (item.number as number),
        type: str(item.type),
        title: str(item.title),
        review: typeof item.review === "boolean" ? item.review : null,
        deferred: item.deferred === true,
        review_at: isNumber(item.review_at) ? item.review_at : null,
        locked: item.locked === true,
      })),
    after: numbersBy(raw.after),
    proposed: numbersBy(raw.proposed),
    current: isRecord(raw.current) ? numbersBy(raw.current) : null,
    loose: list(raw.loose).filter(isNumber),
    ready: list(raw.ready).filter(isNumber),
    problems: list(raw.problems).filter((p): p is string => typeof p === "string"),
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
