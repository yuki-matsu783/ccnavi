/**
 * マージ状況の確認が読む2つの答えの形と、その検証。VS CodeにもDOMにも触れない。
 *
 * - `ccnavi-review.sh mr-state <番号>`（読み取りだけ。shがGitHub / GitLabに聞く）
 * - `ccnavi sync retired`（片付けの記録`retire/<親>.json`を持つ親の一覧。ネットワークにもgitにも触れない）
 *
 * どちらも**厳しく読む**。形が想定外のものを「マージ待ち」「マージ済み」「ありません」と読み間違えると、
 * ユーザが片付けを起こしたり、確認すべき親を見落としたりするため。読めないものは「出力の形が想定外」「一覧を取得できません」と
 * 正直に言い、行を黙って破棄しない。
 */

/** `mr-state`の1行目。頭の行の版 */
export const MR_STATE_HEAD = "mr-state 1";

/** shが返す`unknown`の種類 */
export const SH_UNKNOWN_KINDS = ["no-tool", "auth", "network", "api", "not-found"] as const;
export type ShUnknownKind = (typeof SH_UNKNOWN_KINDS)[number];

/**
 * 確認できなかった理由の種類。前の5つはshの答え（`unknown <種類> <理由>`）。残りは拡張の側だけの種類で、
 * shの`unknown`の種類には入れない。
 *
 * - `timeout`: 期限切れ（拡張が1回ごとに付ける期限で打ち切った）
 * - `format`: 出力の形が想定外（頭の行・語の数・番号・Draft・終了コードの食い違いなど）
 * - `failed`: 実行できなかった、またはshが想定外の終了コードで終わった
 * - `no-number`: 片付けの記録にMRの番号が無い（番号が読めない）
 */
export type UnknownKind = ShUnknownKind | "timeout" | "format" | "failed" | "no-number";

export type MrStateResult =
  | { readonly kind: "open"; readonly number: number; readonly draft: boolean; readonly url: string }
  | { readonly kind: "merged"; readonly number: number; readonly url: string }
  | { readonly kind: "closed"; readonly number: number; readonly url: string }
  | { readonly kind: "unknown"; readonly reason: UnknownKind; readonly detail: string };

/** 子プロセスの終わり方。`killed`は期限で打ち切ったとき */
export interface Ran {
  readonly code: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly killed: boolean;
}

/** 状態ごとの項目の数（状態の語を含む）。`unknown`は理由が空白を含むので、下限で見る */
const OPEN_WORDS = 4;
const SETTLED_WORDS = 3;
const UNKNOWN_MIN_WORDS = 3;

function unknown(reason: UnknownKind, detail: string): MrStateResult {
  return { kind: "unknown", reason, detail };
}

/** 数字だけの番号（先頭が0でない）。`Number()`に任せると`1e3`や`0x10`を通す */
function parseNumber(word: string): number | undefined {
  return /^[1-9][0-9]{0,8}$/.test(word) ? Number(word) : undefined;
}

/**
 * `mr-state`の結果を読む。次のすべてを満たすときだけ正常とみなす。
 *
 * - 1行目が`mr-state 1`、2行目の最初の語が既知の状態、ほかの行が無い
 * - 項目の数が状態ごとに決めた数（openは4、merged / closedは3、unknownは3以上）
 * - 要求したMRの番号と同じ
 * - Draftが`yes` / `no`
 * - 終了コードが状態と合う（状態が分かれば0、`unknown`は1）
 *
 * ヒットしなければ「出力の形が想定外」。URLがhttp(s)でないときは状態を出し、リンクにしない（呼び手の仕事）。
 * 期限で打ち切ったときは`timeout`
 */
export function interpretMrState(ran: Ran, expected: number): MrStateResult {
  if (ran.killed) {
    return unknown("timeout", "時間内に終わりませんでした");
  }
  const lines = ran.stdout.split("\n").map((line) => line.replace(/\r$/, ""));
  // 末尾の改行で生まれる空行は無視する。途中の空行は形の異常（語の数で落ちる）
  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  if (lines[0] !== MR_STATE_HEAD) {
    // shが想定外の終了コード（実行できない・落ちた）で終わったのか、答えの形が違うのか
    // 終了コード2は引数の誤りやワークスペース不明。stderrに理由があれば、形の異常ではなく実行の失敗として出す
    const failed = ran.code !== 0 && ran.code !== 1 && (ran.code !== 2 || ran.stderr.trim() !== "");
    return failed
      ? unknown("failed", `shが終了コード${ran.code}で終わりました${tail(ran.stderr)}`)
      : format(lines.length === 0 ? "出力が空です" : "1行目がmr-state 1ではありません");
  }
  if (lines.length !== 2) {
    return format(lines.length < 2 ? "状態の行がありません" : "行が多すぎます");
  }
  const words = lines[1].split(" ");
  const head = words[0];
  switch (head) {
    case "open": {
      if (words.length !== OPEN_WORDS) {
        return format("openの項目の数が違います");
      }
      const number = parseNumber(words[1]);
      if (number === undefined || number !== expected) {
        return format(`要求したMR（#${expected}）と番号が違います（${words[1]}）`);
      }
      if (words[2] !== "yes" && words[2] !== "no") {
        return format(`Draftがyes / noではありません（${words[2]}）`);
      }
      if (ran.code !== 0) {
        return format(`終了コードが状態と合いません（${ran.code}）`);
      }
      return { kind: "open", number, draft: words[2] === "yes", url: words[3] };
    }
    case "merged":
    case "closed": {
      if (words.length !== SETTLED_WORDS) {
        return format(`${head}の項目の数が違います`);
      }
      const number = parseNumber(words[1]);
      if (number === undefined || number !== expected) {
        return format(`要求したMR（#${expected}）と番号が違います（${words[1]}）`);
      }
      if (ran.code !== 0) {
        return format(`終了コードが状態と合いません（${ran.code}）`);
      }
      return { kind: head, number, url: words[2] };
    }
    case "unknown": {
      if (words.length < UNKNOWN_MIN_WORDS) {
        return format("unknownの項目の数が違います");
      }
      const kind = words[1];
      if (!(SH_UNKNOWN_KINDS as readonly string[]).includes(kind)) {
        return format(`unknownの種類が想定外です（${kind}）`);
      }
      if (ran.code !== 1) {
        return format(`終了コードが状態と合いません（${ran.code}）`);
      }
      return unknown(kind as ShUnknownKind, words.slice(2).join(" "));
    }
    default:
      return format(`状態が想定外です（${head}）`);
  }
}

function format(why: string): MrStateResult {
  return unknown("format", `出力の形が想定外です（${why}）`);
}

/** 標準エラーの最初の行（あれば）。理由の末尾に足す */
function tail(stderr: string): string {
  const line = stderr.split("\n").map((l) => l.trim()).find((l) => l !== "");
  return line === undefined ? "" : `: ${line}`;
}

/** 片付けの記録を持つ親1件（`retired <親> <リポジトリ> <MRの番号|-> <ブランチ|->`） */
export interface RetiredEntry {
  readonly parent: string;
  /** `self`かプロジェクトの名前 */
  readonly repo: string;
  /** MRの番号。記録に無い（`-`）か読めなければnull */
  readonly mr: number | null;
  readonly branch: string;
}

/** 片付けの記録が使えない親（`incomplete <親> <理由>`）。確認できないものとして出す */
export interface IncompleteEntry {
  readonly parent: string;
  readonly reason: string;
}

export type RetiredParse =
  | { readonly ok: true; readonly entries: readonly RetiredEntry[]; readonly incomplete: readonly IncompleteEntry[] }
  | { readonly ok: false; readonly error: string };

/** `sync retired`の頭の行 */
export const RETIRED_HEAD = "retired 1";

/**
 * `ccnavi sync retired`の答えを読む。頭の行が`retired 1`でない・コマンドが失敗した（`code`が0でない）・
 * 知らない行がある・`retired`の行の項目が足りない、のどれかなら「一覧を取得できません」（`ok: false`）にする。
 * 「ありません」とは言わず、行を黙って破棄しない
 */
export function parseRetired(ran: Pick<Ran, "code" | "stdout" | "killed">): RetiredParse {
  if (ran.killed) {
    return { ok: false, error: "一覧を取得できません（時間内に終わりませんでした）" };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `一覧を取得できません（コマンドが失敗しました。終了コード${ran.code}）` };
  }
  const lines = ran.stdout.split("\n").map((line) => line.replace(/\r$/, ""));
  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  if (lines[0] !== RETIRED_HEAD) {
    return { ok: false, error: "一覧を取得できません（形式の異常: 頭の行がretired 1ではありません）" };
  }
  const entries: RetiredEntry[] = [];
  const incomplete: IncompleteEntry[] = [];
  for (const [i, line] of lines.slice(1).entries()) {
    const at = `${i + 2}行目`;
    const words = line.split(" ");
    if (words[0] === "retired") {
      if (words.length !== 5 || words.slice(1, 5).some((w) => w === "")) {
        return { ok: false, error: `一覧を取得できません（形式の異常: ${at}のretiredの項目が足りません）` };
      }
      const mr = parseNumber(words[3]);
      entries.push({ parent: words[1], repo: words[2], mr: mr ?? null, branch: words[4] === "-" ? "" : words[4] });
    } else if (words[0] === "incomplete") {
      if (words.length < 2 || words[1] === "") {
        return { ok: false, error: `一覧を取得できません（形式の異常: ${at}のincompleteに親がありません）` };
      }
      incomplete.push({ parent: words[1], reason: words.slice(2).join(" ") });
    } else {
      return { ok: false, error: `一覧を取得できません（形式の異常: ${at}が想定外です）` };
    }
  }
  return { ok: true, entries, incomplete };
}

/** リポジトリの名前（`self`かプロジェクトの名前）から、ボードの`project`（ワークスペースは空）を引く */
export function projectOfRepo(repo: string): string {
  return repo === "self" ? "" : repo;
}

/**
 * リポジトリのディレクトリ。`ccnavi-sync.sh`の`repo_dir_of`と同じ規則: `self`はワークスペースルート、
 * プロジェクトは設定の`projects`（絶対パスか`C:`で始まるならそのまま、そうでなければルートからの相対）の下
 */
export function repoDirOf(root: string, projects: string, repo: string): string {
  const base = root.replace(/\\/g, "/").replace(/\/+$/, "");
  if (repo === "self") {
    return base;
  }
  const dir = projects.replace(/\\/g, "/").replace(/\/+$/, "");
  return /^(\/|[A-Za-z]:)/.test(dir) ? `${dir}/${repo}` : `${base}/${dir}/${repo}`;
}
