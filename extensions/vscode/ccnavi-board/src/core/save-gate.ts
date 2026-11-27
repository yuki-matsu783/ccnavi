/**
 * 設定を直す画面（ルール管理・リスク管理・フェーズ管理・フロー編集・テンプレートの編集）の［保存］を押せるかと、押せない理由。
 * ボタンの`disabled`・ツールチップ・Ctrl+Sは、どれもこの1か所の答えを使う（画面ごとに条件を書き直さない）。
 * 計画編集は条件が違う（実行ファイルの答えで決まる）ので`workflow-view.ts`の`saveBlocker`が持つ。
 *
 * 理由は上から最初に当たったものを1つだけ返す。直しようのない状態、直せば通る状態、変更が無い状態の順に並べる。
 */
export interface EditSaveState {
  /** 実行ファイルへの往復の途中 */
  readonly busy: boolean;
  /** 保存するものがある（新しく作るファイルは、変更が無くてもある） */
  readonly dirty: boolean;
  /** 書き込みの錠。`reason`が空なら、確かめられていないとみなす */
  readonly lock: { readonly locked: boolean; readonly reason: string };
  /** 読み取り専用で開いている理由。空なら書ける */
  readonly readOnly?: string;
  /** 編集中の中身が保存できない形である理由。空なら保存できる形 */
  readonly invalid?: string;
}

/** 押せない理由。空なら押せる */
export function editSaveBlocker(state: EditSaveState): string {
  if (state.busy) {
    return "処理中のため保存できません。終わるまでお待ちください";
  }
  if (state.readOnly !== undefined && state.readOnly !== "") {
    return state.readOnly;
  }
  if (state.lock.locked) {
    return state.lock.reason === "" ? "保存できるか確認できていないため、保存できません" : state.lock.reason;
  }
  if (state.invalid !== undefined && state.invalid !== "") {
    return state.invalid;
  }
  if (!state.dirty) {
    return "変更が無いため、保存するものがありません";
  }
  return "";
}

/** 押せるときのツールチップ。押せないときは理由を出す */
export function saveTitle(blocker: string): string {
  return blocker === "" ? "保存します（Ctrl+S）" : blocker;
}
