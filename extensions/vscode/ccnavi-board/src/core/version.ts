/**
 * `ccnavi --version --json`（版の JSON）の読み方と、拡張との食い違いの見分け方。
 * 形の定義は ccnavi の README「版の JSON」。VS Code の API は使わない（単体テストで確かめる）。
 *
 * 見るのは 2 つ。
 *
 * - **互換の版（`compat`）**。実行ファイルと、それを呼ぶ側（拡張と `.ccnavi/scripts/` の sh）の契約の版。
 *   拡張は起動のときに自分の `EXTENSION_COMPAT` と比べ、違えばどちらを新しくするかを名指しする
 * - **受け付けるフラグ（`flags`）**。新しいフラグ（`--flow` など）を使う前に、実行ファイルが知っているかを見る。
 *   前は渡してみて argparse の「知らないオプション」の苦情で見分けていた（フラグごとに渡してみる形）
 *
 * `--version` を知らない実行ファイルは、この仕組みより前の古い版として扱う。
 */

/** 拡張が読める版の JSON の形の版（`schema`） */
export const VERSION_SCHEMA = 1;

/**
 * 拡張が頼る実行ファイルの契約の版。src/ccnavi/entry/version.py の COMPAT、ccnavi-common.sh の CCNAVI_COMPAT と揃える
 * （tests/sh/test_compat_skew.py が 3 か所を見比べる）。上げ方は src/ccnavi/entry/version.py の説明のとおり
 */
export const EXTENSION_COMPAT = 4;

export interface VersionInfo {
  readonly schema: number;
  readonly version: string;
  /** 組み立ての元のコミット。ソースで動いていれば `unknown` */
  readonly commit: string;
  readonly built: boolean;
  readonly compat: number;
  readonly flags: readonly string[];
}

/**
 * 実行ファイルに `--version --json` を聞いた結果。
 * `old` は `--version` を知らない（この仕組みより前の）実行ファイル。
 */
export type VersionProbe =
  | { readonly kind: "ok"; readonly info: VersionInfo }
  | { readonly kind: "old" }
  | { readonly kind: "failed"; readonly error: string };

export type ParsedVersion = { readonly ok: true; readonly value: VersionInfo } | { readonly ok: false; readonly error: string };

export function parseVersionJson(text: string): ParsedVersion {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  const r = raw as Record<string, unknown>;
  if (r.schema !== VERSION_SCHEMA) {
    return { ok: false, error: `版の JSON の形が違います（拡張は ${VERSION_SCHEMA}、実行ファイルは ${String(r.schema)}）` };
  }
  if (typeof r.compat !== "number" || !Array.isArray(r.flags)) {
    return { ok: false, error: "版の JSON に compat か flags がありません" };
  }
  return {
    ok: true,
    value: {
      schema: r.schema,
      version: typeof r.version === "string" ? r.version : "",
      commit: typeof r.commit === "string" ? r.commit : "unknown",
      built: r.built === true,
      compat: r.compat,
      flags: r.flags.filter((f): f is string => typeof f === "string"),
    },
  };
}

/**
 * 実行ファイルを新しくする直し方。ccnavi のリポジトリ（build.py とソースがある）なら組み立て直し、
 * 配布先なら ccnavi のリポジトリから配り直し。sh（ccnavi-common.sh）と `--lint` と同じ言い分け
 */
export function rebuildHint(fromSource: boolean): string {
  return fromSource
    ? "build.py を実行して組み立て直してください（uv run --with pyinstaller python build.py）"
    : "ccnavi のリポジトリで build.py を実行し、scripts/ccnavi-setup.sh <このワークスペース> --force で配り直してください";
}

/** 拡張を新しくする直し方 */
export const UPDATE_EXTENSION_HINT =
  "拡張を新しくしてください（ccnavi のリポジトリの extensions/vscode/ccnavi-board で pnpm package して入れ直す）";

/** 表示に使う実行ファイルの版とコミット（`0.1.0 abc1234`） */
function named(info: VersionInfo): string {
  const commit = info.commit === "unknown" ? "コミット不明" : info.commit.slice(0, 12);
  return `${info.version} ${commit}`;
}

/**
 * 起動のときに言う食い違い。揃っていれば undefined。聞けなかった（`failed`）ときも言わない
 * （起動の知らせは食い違いだけにする。実行ファイルが無い・壊れているは画面を開いたときに言う）
 */
export function skewMessage(probe: VersionProbe, fromSource: boolean): string | undefined {
  if (probe.kind === "old") {
    return `ccnavi の実行ファイルが古い版です（--version に対応していません）。${rebuildHint(fromSource)}`;
  }
  if (probe.kind === "failed") {
    return undefined;
  }
  const { info } = probe;
  if (info.compat < EXTENSION_COMPAT) {
    return `ccnavi の実行ファイル（${named(info)}）は互換 ${info.compat}、拡張は互換 ${EXTENSION_COMPAT} で、実行ファイルが古い版です。${rebuildHint(fromSource)}`;
  }
  if (info.compat > EXTENSION_COMPAT) {
    return `ccnavi の実行ファイル（${named(info)}）は互換 ${info.compat}、拡張は互換 ${EXTENSION_COMPAT} で、拡張が古い版です。${UPDATE_EXTENSION_HINT}`;
  }
  return undefined;
}

/**
 * フラグを使う前に、実行ファイルが知っているかを見る。知っていれば undefined、知らなければ理由。
 * `what` は使おうとしたもの（`ccnavi --lint --json --flow`）。確かめられないものは進めない扱いにする
 */
export function missingFlags(probe: VersionProbe, flags: readonly string[], what: string, fromSource: boolean): string | undefined {
  if (probe.kind === "failed") {
    return `実行ファイルの版を確かめられないので ${what} を使いません: ${probe.error}`;
  }
  if (probe.kind === "old") {
    return `実行ファイルが古い版です（--version に対応していません）。${what} で確かめられないので進めません。${rebuildHint(fromSource)}`;
  }
  const missing = flags.filter((flag) => !probe.info.flags.includes(flag));
  if (missing.length === 0) {
    return undefined;
  }
  return `実行ファイル（${named(probe.info)}）が ${missing.join(" ")} に対応していません（古い版です）。${what} で確かめられないので進めません。${rebuildHint(fromSource)}`;
}
