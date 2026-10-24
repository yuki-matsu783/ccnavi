/**
 * ユーザの判断をターミナルへ送るときのコマンド行と、承認を子プロセスで打つときの引数の配列。
 *
 * 承認と残った指摘の行き先は、ボードのオーバーレイでユーザが押したものを、拡張が子プロセスで打つ
 * （`user agree --yes <識別子,…>`、`ccnavi-user-decide.sh decide <N> --choices …`）。ターミナルの壁は無く、
 * 代わりに「見せたものと今のものが同じ」ことを実行ファイルがダイジェストで求める。エージェントが Bash で
 * 同じ形を打つ経路は、実行ファイルの組み込みの deny が止める。`close-early` はターミナル（tty）から打つもので、
 * ボードには置かない。
 *
 * 承認が通ったあと、承認済みチケットをコミットして push する sh（`ccnavi-user-push-approved.sh`）は
 * ターミナルに Enter まで送る。承認と同時にターミナルで走り、ユーザはターミナルでその結果を見る。
 */
import * as path from "node:path";

/** 承認の push の sh の、ワークスペースルートからのパス */
export const PUSH_APPROVED_SCRIPT = ".ccnavi/scripts/ccnavi-user-push-approved.sh";

/** ccnavi の起動の仕方。実行ファイルがあればそれ、無ければソースを uv で走らせる */
export type Launcher =
  | { readonly kind: "exe"; readonly path: string }
  | { readonly kind: "uv"; readonly root: string };

/** bash の単引用符で囲む。中の単引用符は '\'' に置き換える */
export function shellQuote(text: string): string {
  return `'${text.replace(/'/g, `'\\''`)}'`;
}

/** Windows の区切りを "/" にする。Git Bash は `C:/Users/...` を読める */
export function toPosixPath(filePath: string): string {
  return filePath.replace(/\\/g, "/");
}

/**
 * `--agree --preview --json [<識別子>...]`。一覧を見るだけで承認済みチケットは置かない（子プロセスの引数）。
 * 識別子を並べればその分だけが対象、空なら承認待ち全部が対象。ボードは絞り込みで見えている分を渡す。
 */
export function previewArgs(tickets: readonly string[] = []): readonly string[] {
  return ["--agree", "--preview", "--json", ...tickets];
}

/**
 * `user agree --yes <識別子,…> --digest <ダイジェスト> --json [<絞り>...]`。見せた一覧をそのまま承認する（子プロセスの引数）。
 * `tickets` はオーバーレイに出ていた識別子、`digest` はそのとき見せたダイジェスト（提案の識別子の一覧と各提案ファイルのバイト列全体。preview の `digest`）、
 * `only` はそのとき preview に渡した絞り。
 * 絞りを渡さないと、実行ファイルは「絞らないときの対象」と見せた識別子を比べるので、
 * 絞り込み中の承認がいつも食い違いになる。ダイジェストを渡さないと、実行ファイルは承認しない。
 */
export function approveArgs(
  tickets: readonly string[],
  digest: string,
  only: readonly string[] = [],
): readonly string[] {
  return ["user", "agree", "--yes", tickets.join(","), "--digest", digest, "--json", ...only];
}

/**
 * `--plan-order <親> --json [--order <JSON>]`。計画編集タブが計画の図を読む。
 * `order` は元の番号（いま提案に書いてある番号）での直接の先行で、線を変えたときだけ付ける（空なら提案のまま）。
 * 番号の振り直しと順序の検査は実行ファイルが答える
 */
export function planOrderArgs(parent: string, order?: Readonly<Record<string, unknown>>): readonly string[] {
  const base = ["--plan-order", parent, "--json"];
  return order === undefined || Object.keys(order).length === 0 ? base : [...base, "--order", JSON.stringify(order)];
}

/** `--templates --json --layer <名前>`。テンプレート管理画面が一覧を読む。`--root` は `run()` が付ける */
export function templatesArgs(layer: string): readonly string[] {
  return ["--templates", "--json", "--layer", layer];
}

/** `--templates --show <名前> --layer <名前> --json`。1 件の本文を読む */
export function templatesShowArgs(name: string, layer: string): readonly string[] {
  return ["--templates", "--show", name, "--layer", layer, "--json"];
}

/** `--templates --check-file <パス> --layer <名前> --json`。編集中の 1 本を読ませる（何も書かない）。ファイル名の拡張子を除いたものが名前の期待値 */
export function templatesCheckArgs(file: string, layer: string): readonly string[] {
  return ["--templates", "--check-file", file, "--layer", layer, "--json"];
}

/**
 * `--templates --draft <親> --json`。完了した親から下書きを作る（効力の無い下書きを書くだけ）。`--root` は `run()` が付ける
 */
export function templatesDraftArgs(parent: string): readonly string[] {
  return ["--templates", "--draft", parent, "--json"];
}

/**
 * `user handoff [<パス>…] --json`。`wip/handoff/` に置いた下書きの一覧を見る（`--yes` を付けない）。
 * パスは `--root` で指したツリーからの相対（`/` 区切り）。パスを渡すと、そのパスが選んだ対象になり、
 * 答えの `expect` はその集合のハッシュになる。パスを省くと、許可リストにヒットし unchanged でないもの全部が対象。
 * `--root` は `run()` が付ける。
 */
export function handoffArgs(paths: readonly string[] = []): readonly string[] {
  return ["user", "handoff", ...paths, "--json"];
}

/**
 * `user handoff <パス…> --yes --expect <ハッシュ> --via board --json`。取り込みの実行。
 * `expect` は、同じパスを指して見るだけを呼んだ答えの `expect`。変わっていれば実行ファイルは何も書かない。
 * `--via board` で、ボードから来た実行だと実行ファイルに伝える（`defense` の種類はこの経路では断られる）
 */
export function handoffYesArgs(paths: readonly string[], expect: string): readonly string[] {
  return ["user", "handoff", ...paths, "--yes", "--expect", expect, "--via", "board", "--json"];
}

/**
 * `user mirror [<プロジェクト名>…] --json`。共通レイヤーのミラーのずれを見る（`--yes` を付けない。何も書かない）。
 * 名前を省くと全プロジェクト。結果の `expect` は差分のある全プロジェクトの、各プロジェクトの `expect` はそのプロジェクトだけのハッシュ。
 * `--root` は `run()` が付ける。
 */
export function mirrorArgs(names: readonly string[] = []): readonly string[] {
  return ["user", "mirror", ...names, "--json"];
}

/**
 * `user mirror [<プロジェクト名>…] --yes --expect <値> --via board --json`。ミラーの同期。書くのは各プロジェクトのワークツリーで、コミットはしない。
 * 名前を省くと差分のある全プロジェクト。`expect` は見るだけの結果の `expect`（全体）か、各プロジェクトの `expect`（単独）。
 * 変わっていれば実行ファイルは何も書かない
 */
export function mirrorYesArgs(names: readonly string[], expect: string): readonly string[] {
  return ["user", "mirror", ...names, "--yes", "--expect", expect, "--via", "board", "--json"];
}

/**
 * `--plan-order <親> --order <JSON> --expect <ハッシュ> --write --json`。計画編集タブの保存。
 * `expect` は開いたときの `source_sha`。親か子の提案が外で変わっていれば、実行ファイルは何も書かない。
 * `--write` は `--order` を求めるので、空でも付ける
 */
export function planOrderWriteArgs(parent: string, order: Readonly<Record<string, unknown>>, expect: string): readonly string[] {
  return ["--plan-order", parent, "--order", JSON.stringify(order), "--expect", expect, "--write", "--json"];
}

/** レビューの sh の、ワークスペースルートからのパス */
export const REVIEW_SCRIPT = ".ccnavi/scripts/ccnavi-review.sh";
/** 残った指摘の対応方針を書く側の sh（ユーザ専用）の、ワークスペースルートからのパス。読む側（`decide <N> --preview`）は REVIEW_SCRIPT に残る */
export const USER_DECIDE_SCRIPT = ".ccnavi/scripts/ccnavi-user-decide.sh";
/** 子のワークツリーを承認の後に作る sh（`worktree add` を通す）。承認の文面と失敗の案内も同じ呼び方を使う */
export const GIT_SCRIPT = ".ccnavi/scripts/ccnavi-git.sh";

/**
 * `ccnavi-review.sh decide <N> --preview`。残った指摘とダイジェストを JSON で見る。
 * sh は実行した場所を親のワークツリーとして実行ファイルに渡すので、子プロセスの cwd を親のワークツリーにする。
 * `.ccnavi/scripts/` はワークスペースにしか無く、プロジェクトから切ったワークツリーには届かないので、
 * sh はワークスペースルートからのパスで書く（呼ぶ側が `REVIEW_SCRIPT` を root に足す）
 */
export function decidePreviewArgs(phase: number): readonly string[] {
  return ["decide", String(phase), "--preview"];
}

/**
 * `ccnavi-user-decide.sh decide <N> --choices <JSON> --digest <ダイジェスト>`。ユーザがオーバーレイで選んだ行き先を置く。
 * ダイジェストは見せたときの preview の `digest`。見せたあとに指摘が変わっていれば、実行ファイルは何も置かない。
 * エージェントがこの形を打つと、組み込みの deny（builtin:guard-ticket-approval）が止める
 */
export function decideArgs(
  phase: number,
  choices: Readonly<Record<string, string>>,
  digest: string,
): readonly string[] {
  return ["decide", String(phase), "--choices", JSON.stringify(choices), "--digest", digest];
}

/**
 * 文面で案内する `.ccnavi/scripts/` の sh のパス。実行ファイルの `settings.script_command` と同じ引用の規則で、
 * ワークスペースルートから `/` 区切りで書き、空白やシェルの記号を含むときだけ引用する。引用しないと
 * sh が単語に分け、止めている間の例外（`\S*ccnavi-...`）にもヒットしない。まず `"..."`、`"` の中でも意味を持つ
 * 文字があるときだけ単引用符にする。
 * 実行ファイルは root を realpath で解いてから組む。ここは渡されたパスをそのまま使うので、実行ファイルの
 * 案内と同じパスにしたい呼び手は、解いた root を渡す（board-panel が fs.realpathSync で解く）。
 */
export function scriptCommand(root: string, name: string): string {
  const base = toPosixPath(root).replace(/\/+$/, "");
  const script = `${base}/.ccnavi/scripts/${name}`;
  if (/^[^\s'"\\$`!*?\[\]{}()<>|&;#~]+$/.test(script)) {
    return `sh ${script}`;
  }
  if (!/["\\$`!]/.test(script)) {
    return `sh "${script}"`;
  }
  return `sh ${shellQuote(script)}`;
}

/**
 * 対応方針を決める画面の「エージェントに相談するプロンプトをコピー」が組むプロンプト。
 * 未解決の指摘を 1 件ずつ要約させ、対応方針の案と理由を返させる。**読み取りだけ**を頼む:
 * 渡す操作は `ccnavi-review.sh decide <N> --preview` だけで、対応方針を確定する `ccnavi-user-decide.sh`・
 * `ccnavi-review.sh confirm`・そのほかの状態を変える操作は実行させない。決めるのはユーザで、ボードの「レビュー完了」から行う。
 *
 * `--preview` はレビューで止まっている間も通る読み取り専用の形。cwd が親のワークツリーでなければ、
 * `cd` と連結せず、先に `cd` だけを別の Bash で実行させる（連結した形は止まる）。
 */
export function consultPrompt(input: {
  readonly root: string;
  readonly parent: string;
  readonly phase: number;
  readonly mrUrl: string;
  readonly parentTree: string;
}): string {
  const { root, parent, phase, mrUrl } = input;
  const tree = toPosixPath(input.parentTree);
  const lines = [
    `[ccnavi] 親 ${parent} のフェーズ ${phase} のレビューで残った未解決（Unresolved）の指摘について、ユーザが対応方針を決めるための相談に乗る。`,
    `- 親: ${parent}`,
    `- フェーズ: ${phase}`,
  ];
  if (mrUrl !== "") {
    lines.push(`- マージリクエスト: ${mrUrl}`);
  }
  lines.push(
    `- 親のワークツリー: ${tree}`,
    `親のワークツリー ${tree} で、読み取り専用の '${scriptCommand(root, "ccnavi-review.sh")} decide ${phase} --preview' だけを単体の Bash で実行する` +
      `（cwd が ${tree} でなければ、先に cd だけを別の Bash で実行する。cd や他のコマンドと連結しない）。` +
      "未解決の指摘を 1 件ずつ要約し、対応方針の案（対応しない／このフェーズで直す／issue に回す）と理由を返す。",
    "対応方針を確定する ccnavi-user-decide.sh、ccnavi-review.sh confirm、その他の状態を変える操作は実行しない。" +
      "決めるのはユーザで、ボードの『レビュー完了』から行う。",
    `前の会話が無くても、状況は '${scriptCommand(root, "ccnavi-ticket.sh")} status ${parent}' で確認する（置き場のファイルを自分で探さない）。`,
  );
  return lines.join("\n");
}

/**
 * `ccnavi-review.sh mr-state <番号>`。マージリクエストの状況を読むだけの副命令（書かない）。sh は実行した場所のリポジトリの
 * origin を見るので、子プロセスの cwd は片付けの記録が指すリポジトリにする
 */
export function mrStateArgs(number: number): readonly string[] {
  return ["mr-state", String(number)];
}

/** `ccnavi sync retired`。片付けの記録（`retire/<親>.json`）を持つ親の一覧。ネットワークにも git にも触れない。`--root` は `run()` が付ける */
export function syncRetiredArgs(): readonly string[] {
  return ["sync", "retired"];
}

/** 引用が要らない語（英数字と `._/-:` だけ）。それ以外を含むときだけ単引用符で囲む */
function shellWord(text: string): string {
  return /^[A-Za-z0-9._/:@%+=-]+$/.test(text) ? text : shellQuote(text);
}

/**
 * マージ済みの親の片付けをターミナルに送るコマンド行: `cd <ワークスペースルート> && sh <ルート>/.ccnavi/scripts/ccnavi-sync.sh <親>`。
 * ターミナルは使い回すので、前のコマンドが別の場所へ cd していても届くよう、`cd` から始める。
 * ルートも sh のパスも親も単引用符で引用する（空白・引用符・シェルの記号を含むパスでも壊れない）。
 * ボードが打つのは**このコマンドの送信まで**。MR がマージ済みかの確かめ直しと、条件を満たすものだけの片付けは
 * `ccnavi-sync.sh` が決める（ボードの確認を信用して片付けない）
 */
export function retireCommand(root: string, parent: string): string {
  const base = toPosixPath(root).replace(/\/+$/, "");
  return `cd ${shellQuote(base)} && sh ${shellQuote(`${base}/.ccnavi/scripts/ccnavi-sync.sh`)} ${shellQuote(parent)}`;
}

/** 片付けの確認のダイアログの文。sync が何をするか（リモートの取り込み・マージ済みの確かめ直し・条件を満たすものだけの片付け）を先に言う */
export function retireConfirmText(parent: string): string {
  return (
    `ccnavi-sync.sh ${parent} を実行します。sync はリモートの取り込みも行い、MR がマージ済みかを確かめ直したうえで、` +
    "条件を満たすものだけを片付けます。結果はターミナルで確認してください。"
  );
}

/**
 * マージせずに閉じた親（破棄した親）の、片付けの記録を消すコマンド: `sh <ルート>/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon <親>`。
 * **ボードはこれを送らない**。コピーするだけで、ユーザがターミナルで実行する
 */
export function abandonCommand(root: string, parent: string): string {
  return `${scriptCommand(root, "ccnavi-launcher.sh")} worktree retire --abandon ${shellWord(parent)}`;
}

/** `ccnavi-git.sh` を sh で呼ぶ前置き（ワークスペースルートの絶対パス。空白があれば引用する） */
export function gitScriptCommand(root: string): string {
  return `sh ${shellQuote(path.posix.join(toPosixPath(root), GIT_SCRIPT))}`;
}

/**
 * `ccnavi-user-push-approved.sh`。承認済みチケットをコミットして push する。ワークスペースルートから打つ。
 * 絶対パスで組む。ターミナルは使い回すので、前のコマンドが別の場所へ cd していても届く。
 * `parents` を渡すとその親子のチケットだけをコミットして push する（取り込み済みのものだけが送られ、
 * そうでないものは今どおりユーザがコミットする）。
 * `paths`（ワークスペースルートからの相対）を渡すと、置き場の変更のうちそのパスだけを送る（`--path`）。
 * 承認が書いた・消したパスを渡し、承認と無関係な変更を承認のコミットに入れない。`parents` とは同時に指定できない
 */
export function pushApprovedCommand(
  root: string,
  parents: readonly string[] = [],
  paths: readonly string[] = [],
): string {
  const names = parents.map((p) => ` ${shellQuote(p)}`).join("");
  const only = paths.map((p) => ` --path ${shellQuote(p)}`).join("");
  return `sh ${shellQuote(path.posix.join(toPosixPath(root), PUSH_APPROVED_SCRIPT))}${names}${only}`;
}

/**
 * `ccnavi write-and-push family <親>` の答えから `target`（`yes` / `no` / `stop`）を読む。頭の `write-and-push 1` が無ければ
 * 空文字（古い実行ファイル）。行末の CR は除く
 */
export function writeAndPushTargetOf(stdout: string): string {
  const lines = stdout.split("\n").map((line) => line.replace(/\r$/, ""));
  if (lines[0] !== "write-and-push 1") {
    return "";
  }
  const found = lines.find((line) => line.startsWith("target "));
  return found === undefined ? "" : found.slice("target ".length).trim();
}

/**
 * 判定と検証に掛けるルールファイルの差し替え。共通の設定のルールは `--rules` で、
 * プロジェクト 1 つのルールは `--project-rules-file <名前>=<パス>` で（docs/requirements/json-shapes.md「lint の JSON」）。
 * ワークスペースの設定は同じオプションに名前 `self` で渡す。実行ファイルはレイヤー（layer）の名前で差し替えを引き、
 * `self` という名前のプロジェクトはプロジェクトの設定として数えないので取り違えない。
 * どれも診断（`--lint` / `--test` / `--test-expectations` / `--explain`）でだけ有効で、
 * hook からの判定にもチケットとレビューのサブコマンドにも届かない（実行ファイルが診断以外では断る）。
 * 拡張がこれらを足すのは `--lint` と `--test` だけなので、そこは変わらない。
 */
export type RulesOverride =
  | { readonly kind: "workspace"; readonly path: string }
  | { readonly kind: "project"; readonly name: string; readonly path: string }
  | { readonly kind: "self"; readonly path: string };

/**
 * 検証（`--lint`）に掛ける設定の差し替え。ルールに加えて、共通の設定のリスクの配点を `--risk` で、
 * ワークスペースの設定（`self`）かプロジェクトの設定の配点を `--project-risk-file <名前>=<パス>` で（共通の設定と合わせて検証される）、
 * 同じくフェーズ定義を `--project-phases-file <名前>=<パス>` で差し替えられる。
 * 実行ファイルは差し替えた定義を共通の設定の定義と足した形で確かめる（同じ id で中身が違う、表示名の重なり）。
 * 共通の設定の定義そのものは、この画面からは差し替えない（`--phases` は使わない）。
 * 判定（`--test`）には配点も定義も関係ないので、そちらは RulesOverride だけを受ける。
 */
export type LintOverride =
  | RulesOverride
  | { readonly kind: "risk"; readonly path: string }
  | { readonly kind: "layerRisk"; readonly name: string; readonly path: string }
  | { readonly kind: "phases"; readonly path: string }
  | { readonly kind: "layerPhases"; readonly name: string; readonly path: string };

export function overrideArgs(override: LintOverride): string[] {
  switch (override.kind) {
    case "workspace":
      return ["--rules", override.path];
    case "project":
      return ["--project-rules-file", `${override.name}=${override.path}`];
    case "self":
      return ["--project-rules-file", `self=${override.path}`];
    case "risk":
      return ["--risk", override.path];
    case "layerRisk":
      return ["--project-risk-file", `${override.name}=${override.path}`];
    case "phases":
      return ["--phases", override.path];
    case "layerPhases":
      return ["--project-phases-file", `${override.name}=${override.path}`];
  }
}
