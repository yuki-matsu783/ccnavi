/**
 * 設定ファイルを読み込めなかったときの失敗。ファイルが分かっているときは、エラー画面に
 * ［エディタで開く］を出すために、パスを持たせて投げる。
 * 読めない設定は、画面で推測して直さず、状態と直し方を言って止める（フォームを出さない）。
 */

export interface ErrorFile {
  /** 絶対パス。エディタで開くのに使う */
  readonly path: string;
  /** ワークスペースからの相対パス（`/`区切り）。画面に出す */
  readonly rel: string;
}

export class LoadFailure extends Error {
  constructor(
    message: string,
    readonly file?: ErrorFile,
  ) {
    super(message);
    this.name = "LoadFailure";
  }
}

/** 実行ファイルがこのレイヤーのファイルを読めなかった（`layer.*.unreadable`が空でない） */
export function unreadableFailure(file: ErrorFile, reason: string): LoadFailure {
  return new LoadFailure(`実行ファイルがこのファイルを読み込めません。ファイルを直すか、削除してから更新してください: ${reason}`, file);
}

/** 拡張が読み込みを止める問題（`blockingProblems`など）。理由を行ごとに並べる */
export function blockedFailure(file: ErrorFile, blocking: readonly string[]): LoadFailure {
  return new LoadFailure(`このファイルを読み込めません。ファイルを直してから更新してください:\n${blocking.join("\n")}`, file);
}
