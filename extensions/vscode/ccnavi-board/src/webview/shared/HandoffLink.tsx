/**
 * 取り込み待ち画面へ進むボタン。ルール管理・リスク管理・フェーズ管理・テンプレート管理が同じものを置く
 * （契約は `core/handoff-entry.ts`）。開けない理由（案内の間など）は、ボタンの title と横の注記に出す。
 */
import type { JSX } from "react";
import type { HandoffEntry } from "../../core/handoff-entry.js";

export function HandoffLink(props: {
  /** 読み込む前や案内の見本の間は未指定。ボタンは押せない */
  readonly entry: HandoffEntry | undefined;
  /** 案内の見本の間など、押せなくする */
  readonly disabled?: boolean;
  readonly onOpen: () => void;
}): JSX.Element {
  const { entry, disabled = false, onOpen } = props;
  const enabled = entry?.enabled === true;
  return (
    <>
      <button
        type="button"
        className="action"
        data-action="open-handoff"
        data-tour="handoff"
        disabled={disabled || !enabled}
        title={enabled ? "wip/handoff/ の下書きの取り込みと編集は、取り込み待ちの画面で行います" : entry?.reason ?? ""}
        onClick={onOpen}
      >
        取り込み待ちを開く
      </button>
      {entry !== undefined && !entry.enabled && !disabled && (
        <span className="hint" data-role="handoff-reason">
          {" "}
          {entry.reason}
        </span>
      )}
    </>
  );
}
