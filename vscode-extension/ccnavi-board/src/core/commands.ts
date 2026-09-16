/**
 * 人の判断をターミナルへ送るときのコマンド行と、承認を子プロセスで打つときの引数の並び。
 *
 * `accept` と `wrapup` は端末（tty）から打つものと ccnavi が決めていて、拡張はここで組んだ
 * 1 行をターミナルに送り、y/N は人が押す。承認だけは違う。ボードのオーバーレイで人が押した
 * 承認を、拡張が子プロセスで `--approve --yes <識別子,…>` として打つ。端末の壁は無く、
 * 代わりに「見せた一覧と今の一覧が同じ」ことを実行ファイルが求める。エージェントが Bash で
 * 同じ形を打つ道は、実行ファイルの組み込みの deny が止める。
 *
 * 承認が通ったあと、承認済みチケットをコミットして push する sh（`ccnavi-push-approved.sh`）は
 * ターミナルに Enter まで送る。承認と同時に端末で走り、人は端末でその結果を見る。
 */
import * as path from "node:path";

/** 承認済みチケットを運ぶ sh の、ワークスペースルートからの綴り */
export const PUSH_APPROVED_SCRIPT = ".ccnavi/scripts/ccnavi-push-approved.sh";

/** ccnavi の起動の仕方。実行ファイルがあればそれ、無ければソースを uv で走らせる */
export type Launcher =
  | { readonly kind: "exe"; readonly path: string }
  | { readonly kind: "uv"; readonly root: string };

/** bash の単引用符で囲む。中の単引用符は '\'' に割る */
export function shellQuote(text: string): string {
  return `'${text.replace(/'/g, `'\\''`)}'`;
}

/** Windows の区切りを "/" にする。Git Bash は `C:/Users/...` を読める */
export function toPosixPath(filePath: string): string {
  return filePath.replace(/\\/g, "/");
}

/**
 * `--approve --preview --json [<識別子>...]`。一覧を見るだけで承認済みチケットは置かない（子プロセスの引数）。
 * 識別子を並べればその分だけが対象、空なら承認待ち全部が対象。ボードは絞り込みで見えている分を渡す。
 */
export function previewArgs(tickets: readonly string[] = []): readonly string[] {
  return ["--approve", "--preview", "--json", ...tickets];
}

/**
 * `--approve --yes <識別子,…> --digest <指紋> --json [<絞り>...]`。見せた一覧をそのまま承認する（子プロセスの引数）。
 * `tickets` はオーバーレイに出ていた識別子、`digest` はそのとき見せた指紋（承認画面の本文と承認済みチケットに写る中身。preview の `digest`）、
 * `only` はそのとき preview に渡した絞り。
 * 絞りを渡さないと、実行ファイルは「絞らないときの対象」と見せた識別子を比べるので、
 * 絞り込み中の承認がいつも食い違いになる。指紋を渡さないと、実行ファイルは承認しない。
 */
export function approveArgs(
  tickets: readonly string[],
  digest: string,
  only: readonly string[] = [],
): readonly string[] {
  return ["--approve", "--yes", tickets.join(","), "--digest", digest, "--json", ...only];
}

/**
 * `ccnavi-review.sh accept <N>`。sh がレビューのスレッドを取ってきて、未解決のまま進める
 * ことを人が受け入れる。sh は実行した場所を親のワークツリーとして exe に渡すので、
 * 先に親のワークツリーへ cd する。`.ccnavi/scripts/` はワークスペースにしか無く、プロジェクトから
 * 切ったワークツリーには届かないので、sh はワークスペースルートから綴る。
 */
export function acceptCommand(root: string, parentTree: string, phase: number): string {
  const script = shellQuote(`${toPosixPath(root)}/.ccnavi/scripts/ccnavi-review.sh`);
  return `cd ${shellQuote(toPosixPath(parentTree))} && sh ${script} accept ${phase}`;
}

/**
 * 文面で案内する `.ccnavi/scripts/` の sh の綴り。実行ファイルの `settings.script_command` と同じ引用の規則で、
 * ワークスペースルートから `/` 区切りで書き、空白やシェルの記号を含むときだけ引用する。引用しないと
 * sh が単語に割り、ゲートの例外（`\S*ccnavi-...`）にも当たらない。まず `"..."`、`"` の中でも意味を持つ
 * 文字があるときだけ単引用符に落とす。
 * 実行ファイルは root を realpath で解いてから組む。ここは渡された綴りをそのまま使うので、実行ファイルの
 * 案内と同じ綴りにしたい呼び手は、解いた root を渡す（board-panel が fs.realpathSync で解く）。
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
 * 人がレビューを終えたことを Claude Code に伝える文。ボードの「レビュー済み連絡」が組み、
 * 承認の文と同じ 2 ボタン（コピー / 新しいセッションで開く）で渡す。判定は動かさず、マーカーも置かない。
 * `check` を打ってマーカーを置くのは、この文を受けた親（メインエージェント）で、親のワークツリーで打つ。
 * そこはゲートが閉じているので、通るのは `sh …ccnavi-review.sh …` の形を連結せずに単体で打ったときだけ
 * （設計 §9.8。`cd … && sh …` は止まる）。サブエージェントには同じ形が常に禁止される（§9.12）。文はその 2 つを言う。
 * 未解決が残っていれば `check` が一覧と次の道（解決してもらう・同じフェーズに子を足す・人が accept を打つ）を
 * 返すので、文はそれに従うことだけを言い、道を先取りしない。
 */
export function reviewedPrompt(
  root: string,
  parent: string,
  phase: number,
  label: string,
  parentTree: string,
  mrUrl: string,
): string {
  const lines = [`[ccnavi] 利用者が親 ${parent} のフェーズ ${label || String(phase)} のレビューを終えた。`];
  if (mrUrl !== "") {
    lines.push(`- マージリクエスト: ${mrUrl}`);
  }
  const tree = toPosixPath(parentTree);
  lines.push(
    `親（メインエージェント）が、親のワークツリー ${tree} で '${scriptCommand(root, "ccnavi-review.sh")} check --phase ${phase}' を打ち、` +
      "レビュー済みのマーカーを置く。ゲートが閉じている間はこの形の 1 本だけが通るので、cd や他のコマンドと連結せず、" +
      `単体の Bash で打つ（cwd が ${tree} でなければ、先に cd だけを別の Bash で打つ）。サブエージェントには渡さない。` +
      "未解決の指摘が残っていれば check が一覧と次の道を返すので、それに従う。" +
      "マーカーが置かれてゲートが開いたら、次のフェーズへ進む。",
  );
  return lines.join("\n");
}

/**
 * `ccnavi-push-approved.sh`。承認済みチケットをコミットして push する。ワークスペースルートから打つ。
 * 絶対パスで組む。ターミナルは使い回すので、前に accept が親のワークツリーへ cd していても届く。
 */
export function pushApprovedCommand(root: string): string {
  return `sh ${shellQuote(path.posix.join(toPosixPath(root), PUSH_APPROVED_SCRIPT))}`;
}
