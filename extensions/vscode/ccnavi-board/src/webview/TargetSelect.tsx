/**
 * ルール管理・リスク管理・フェーズ管理・テンプレート管理の、設定の対象を切り替える欄。共通の設定・ワークスペース・プロジェクトの 3 種から選ぶ。
 *
 * 対象が 1 つだけでも欄は出す（プロジェクトが無いとルール管理は「共通の設定」「ワークスペース」の 2 つだけになる。欄が消えると切り替えられることが分からない）。
 *
 * 選んだ値は `onSwitch` で拡張ホストへ送るだけで、画面は自分で切り替えない。未保存の変更があれば拡張ホストが
 * 破棄してよいかを聞き、やめたときは中身を入れ替えないので、欄の値は `page.target` に従ったまま変わらない
 * （制御された `<select>` なので、選び直した値は描き直しで元に戻る）。
 */
import type { JSX } from "react";

import { targetValue, type TargetOption } from "../core/targets.js";

export interface TargetSelectProps {
  readonly target: { readonly kind: string; readonly name: string } | undefined;
  readonly targets: readonly TargetOption[] | undefined;
  readonly disabled?: boolean;
  /** 切り替えで未保存の変更の確認が出ないか（出さない画面は false。既定は出す） */
  readonly confirms?: boolean;
  readonly onSwitch: (kind: string, name: string) => void;
}

export function TargetSelect({ target, targets, disabled, confirms, onSwitch }: TargetSelectProps): JSX.Element | null {
  if (target === undefined || targets === undefined) {
    return null;
  }
  return (
    <label className="target-select">
      設定{" "}
      <select
        id="target"
        data-action="switch-target"
        value={targetValue(target)}
        disabled={disabled === true}
        title={confirms === false ? "見る設定を切り替えます" : "編集する設定を切り替えます。未保存の変更があれば、破棄してよいかを聞きます"}
        onChange={(event) => {
          const picked = targets.find((t) => targetValue(t) === event.target.value);
          if (picked !== undefined) {
            onSwitch(picked.kind, picked.name);
          }
        }}
      >
        {targets.map((t) => (
          <option key={targetValue(t)} value={targetValue(t)}>
            {t.label}
          </option>
        ))}
      </select>
    </label>
  );
}
