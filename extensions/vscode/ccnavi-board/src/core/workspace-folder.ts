/**
 * VS Codeで開いているフォルダ（ワークスペースフォルダ）を、画面の対象にするときの案内の文。
 * 対象は最初のフォルダだけ。複数のフォルダ（マルチルート）に対応する仕組みは持たず、そう言って進める。
 */

/** フォルダが1つも開かれていないとき。`what`は開こうとした画面の名前 */
export function noFolderText(what: string): string {
  return `ワークスペースが開かれていないため、${what}を表示できません`;
}

/** フォルダが複数開かれているとき。`first`は最初のフォルダの名前 */
export function multiFolderText(first: string): string {
  return `フォルダが複数開かれています。最初のフォルダ ${first} だけを対象にします`;
}
