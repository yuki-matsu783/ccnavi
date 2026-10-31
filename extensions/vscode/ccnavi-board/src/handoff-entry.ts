/**
 * 取り込み待ち画面へ進む入口の、拡張ホスト側の共通部分（契約は `core/handoff-entry.ts`）。
 * VS Code の API には触れないが、実行ファイルを呼ぶので単体テストの対象外。
 */
import { handoffTreeKey, type HandoffEntry } from "./core/handoff-entry.js";
import { screens } from "./core/screens.js";

/** 取り込み待ち画面へ進む入口。実機の画面ではいつでも開ける（案内の間だけ、案内の見本が無効にする） */
export const HANDOFF_ENTRY: HandoffEntry = { enabled: true, reason: "" };

/** 画面の「取り込み待ちを開く」の本体。開けない理由があるときは何もしない（理由は画面が出している） */
export async function openHandoffFrom(entry: HandoffEntry | undefined, target: { readonly kind: string; readonly name?: string }): Promise<void> {
  if (entry?.enabled !== true) {
    return;
  }
  await screens().handoff(handoffTreeKey(target));
}
