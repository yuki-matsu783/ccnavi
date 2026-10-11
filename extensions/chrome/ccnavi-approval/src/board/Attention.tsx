/**
 * 「要確認」（`[data-testid=attention]`）。元に戻すコミットでも競合が収まらなかった親子のチケットに出す。
 * ユーザがホストの履歴を確かめたら外す（`onDismiss`。渡さなければ外すボタンを出さない）。
 */
import type { JSX } from "react";

import { ActionButton, Notice } from "./parts.js";

export function Attention({ text, onDismiss }: { text: string; onDismiss?: () => void }): JSX.Element {
  return (
    <div className="attention" data-testid="attention">
      <Notice kind="error" text={`要確認: ${text}`} />
      <p className="note">
        この要確認はこのブラウザにだけ記録している（ほかの承認者には見えない）。ホストの履歴を確かめて直すまで、このブラウザからはこの親子のチケットに書かない
      </p>
      {onDismiss ? (
        <ActionButton action="dismiss" onClick={onDismiss}>
          確かめた（要確認を外す）
        </ActionButton>
      ) : null}
    </div>
  );
}
