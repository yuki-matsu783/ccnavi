/**
 * 人の判断をターミナルへ送るときのコマンド行と、承認を子プロセスで打つときの引数の並び。
 *
 * `accept` と `wrapup` は端末（tty）から打つものと ccnavi が決めていて、拡張はここで組んだ
 * 1 行をターミナルに送り、y/N は人が押す。承認だけは違う。ボードのオーバーレイで人が押した
 * 承認を、拡張が子プロセスで `--approve --yes <識別子,…>` として打つ。端末の壁は無く、
 * 代わりに「見せた束と今の束が同じ」ことを実行ファイルが求める。エージェントが Bash で
 * 同じ形を打つ道は、実行ファイルの組み込みの deny が止める。
 */

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

function ccnaviInvocation(launcher: Launcher, root: string): string {
  const rootArg = shellQuote(toPosixPath(root));
  if (launcher.kind === "exe") {
    return `${shellQuote(toPosixPath(launcher.path))} --root ${rootArg}`;
  }
  return `uv run python -m ccnavi --root ${rootArg}`;
}

/** `--approve --preview --json`。束を見るだけで写しは置かない（子プロセスの引数） */
export function previewArgs(): readonly string[] {
  return ["--approve", "--preview", "--json"];
}

/** `--approve --yes <識別子,…> --json`。見せた束をそのまま承認する（子プロセスの引数） */
export function approveArgs(tickets: readonly string[]): readonly string[] {
  return ["--approve", "--yes", tickets.join(","), "--json"];
}

/**
 * `ccnavi-review.sh accept <N>`。sh がレビューのスレッドを取ってきて、未解決のまま進める
 * ことを人が受け入れる。sh は実行した場所を親の作業ツリーとして exe に渡すので、
 * 先に親の作業ツリーへ cd する。
 */
export function acceptCommand(parentTree: string, phase: number): string {
  return `cd ${shellQuote(toPosixPath(parentTree))} && sh .claude/scripts/ccnavi-review.sh accept ${phase}`;
}
