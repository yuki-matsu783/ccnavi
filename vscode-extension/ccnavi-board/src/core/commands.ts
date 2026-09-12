/**
 * 人の承認をターミナルへ送るときのコマンド行。bash（Windows なら Git Bash）で動く形。
 *
 * 拡張は承認を自分で実行しない。`--approve` と `accept` は端末（tty）から
 * 打つものと ccnavi が決めていて（人の合意をエージェントが出せないための壁）、
 * 拡張の子プロセスもその壁の外に置く。ここで組んだ 1 行をターミナルに送り、y/N は人が押す。
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

/** `ccnavi --approve`。束（いま承認待ちのもの全部）を見せて y/N を取る */
export function approveCommand(launcher: Launcher, root: string): string {
  return `cd ${shellQuote(toPosixPath(root))} && ${ccnaviInvocation(launcher, root)} --approve`;
}

/**
 * `ccnavi-review.sh accept <N>`。sh がレビューのスレッドを取ってきて、未解決のまま進める
 * ことを人が受け入れる。sh は実行した場所を親の作業ツリーとして exe に渡すので、
 * 先に親の作業ツリーへ cd する。
 */
export function acceptCommand(parentTree: string, phase: number): string {
  return `cd ${shellQuote(toPosixPath(parentTree))} && sh .claude/scripts/ccnavi-review.sh accept ${phase}`;
}
