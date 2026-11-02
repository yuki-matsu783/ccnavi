/** 動作モード（`CCNAVI_MODE`）の帯の説明。ルール管理とチケット管理の画面が同じ文を使う */

/**
 * 動作モードの帯に出す説明。実行ファイルが答えたモードをそのまま言い換えるだけで、設定ファイルは読まない。
 * enableと空（答えが取れない）は帯を出さないので空を返す
 */
export function modeBanner(mode: string): string {
  if (mode === "dry-run") {
    return "判定と記録をします。denyやaskにヒットしても、一般の保護はツールの実行を止めません。止まるのは、チケットのゲートと補助の保護です";
  }
  if (mode === "disable") {
    return "判定しません。ルールは使われず、denyやaskにヒットする操作も止まりません";
  }
  return "";
}
