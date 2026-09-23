/**
 * 人の判断をターミナルへ送るときのコマンド行と、承認を子プロセスで打つときの引数の並び。
 *
 * 承認と残った指摘の行き先は、ボードのオーバーレイで人が押したものを、拡張が子プロセスで打つ
 * （`--approve --yes <識別子,…>`、`ccnavi-review.sh decide <N> --choices …`）。端末の壁は無く、
 * 代わりに「見せたものと今のものが同じ」ことを実行ファイルが指紋で求める。エージェントが Bash で
 * 同じ形を打つ道は、実行ファイルの組み込みの deny が止める。`close-early` は端末（tty）から打つもので、
 * ボードには置かない。
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

/** レビューの sh の、ワークスペースルートからの綴り */
export const REVIEW_SCRIPT = ".ccnavi/scripts/ccnavi-review.sh";

/**
 * `ccnavi-review.sh decide <N> --preview`。残った指摘と指紋を JSON で見る（何も置かない）。
 * sh は実行した場所を親のワークツリーとして実行ファイルに渡すので、子プロセスの cwd を親のワークツリーにする。
 * `.ccnavi/scripts/` はワークスペースにしか無く、プロジェクトから切ったワークツリーには届かないので、
 * sh はワークスペースルートから綴る（呼ぶ側が `REVIEW_SCRIPT` を root に足す）
 */
export function decidePreviewArgs(phase: number): readonly string[] {
  return ["decide", String(phase), "--preview"];
}

/**
 * `ccnavi-review.sh decide <N> --choices <JSON> --digest <指紋>`。人がオーバーレイで選んだ行き先を置く。
 * 指紋は見せたときの preview の `digest`。見せたあとに指摘が変わっていれば、実行ファイルは何も置かない。
 * エージェントがこの形を打つと、組み込みの deny（builtin-guard-ticket-approval）が止める
 */
export function decideArgs(
  phase: number,
  choices: Readonly<Record<string, string>>,
  digest: string,
): readonly string[] {
  return ["decide", String(phase), "--choices", JSON.stringify(choices), "--digest", digest];
}

/**
 * 文面で案内する `.ccnavi/scripts/` の sh の綴り。実行ファイルの `settings.script_command` と同じ引用の規則で、
 * ワークスペースルートから `/` 区切りで書き、空白やシェルの記号を含むときだけ引用する。引用しないと
 * sh が単語に割り、止めている間の例外（`\S*ccnavi-...`）にも当たらない。まず `"..."`、`"` の中でも意味を持つ
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
 * `confirm` を打ってマーカーを置くのは、この文を受けた親（メインエージェント）で、親のワークツリーで打つ。
 * そこは止まっているので、通るのは `sh …ccnavi-review.sh …` の形を連結せずに単体で打ったときだけ
 * （設計 §9.8。`cd … && sh …` は止まる）。サブエージェントには同じ形が常に禁止される（§9.12）。文はその 2 つを言う。
 * 未解決が残っていれば `confirm` が一覧と次の道（解決してもらう・同じフェーズに子を足す・人が decide で決める）を
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
    `親（メインエージェント）が、親のワークツリー ${tree} で '${scriptCommand(root, "ccnavi-review.sh")} confirm --phase ${phase}' を打ち、` +
      "レビュー済みのマーカーを置く。レビューで止まっている間はこの形の 1 本だけが通るので、cd や他のコマンドと連結せず、" +
      `単体の Bash で打つ（cwd が ${tree} でなければ、先に cd だけを別の Bash で打つ）。サブエージェントには渡さない。` +
      "未解決の指摘が残っていれば confirm が一覧と次の道を返すので、それに従う。" +
      "マーカーが置かれて止まらなくなったら、次のフェーズへ進む。",
  );
  return lines.join("\n");
}

/**
 * `ccnavi-push-approved.sh`。承認済みチケットをコミットして push する。ワークスペースルートから打つ。
 * 絶対パスで組む。ターミナルは使い回すので、前のコマンドが別の場所へ cd していても届く。
 */
export function pushApprovedCommand(root: string): string {
  return `sh ${shellQuote(path.posix.join(toPosixPath(root), PUSH_APPROVED_SCRIPT))}`;
}
