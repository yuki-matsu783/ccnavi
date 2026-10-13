/**
 * 「要確認」（`[data-testid=attention]`）。元に戻すコミットでも競合が収まらなかった親子のチケットに出す。
 * ユーザがホストの履歴を確かめたら外す（`onDismiss`。渡さなければ外すボタンを出さない）。
 */
import type { JSX } from "react";

import { ActionButton, Notice } from "./parts.js";

export function Attention({ text, onDismiss }: { text: string; onDismiss?: () => void }): JSX.Element {
  return (
    <div className="attention" data-testid="attention">
      <Notice kind="error" text={`確認が必要：${text}`} />
      <p className="note">
        この警告は、このブラウザにのみ保存されています。他の承認者には表示されません。ホスト側の履歴を確認し、問題を解消するまで、このブラウザからこの親子チケットへの変更は行いません。
      </p>
      {onDismiss ? (
        <ActionButton action="dismiss" onClick={onDismiss}>
          確認済みとして警告を解除
        </ActionButton>
      ) : null}
    </div>
  );
}
