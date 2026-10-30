/**
 * 「統合結果」（読み取り専用）。使われるテンプレートは共通の設定との和なので、統合結果（共通 + ワークスペース）と、
 * プロジェクトの統合結果（共通の設定 + そのプロジェクトの設定）を、実行ファイルが出した索引のまま並べる。
 *
 * ここは見せるだけ。使われるか・破棄した理由は実行ファイルの答えのままで、拡張は判定も合成もしない。
 * 1 件の中身（計画・フロー・本文）は上の一覧（開いている対象の層）で開く。
 */
import { useState, type JSX } from "react";

import { layersText, sumLabel } from "../../core/sums.js";
import type { Sum as SumData } from "../../core/templates-view.js";
import { droppedLabel, layerLabel } from "./text.js";

export interface SumProps {
  readonly sums: readonly SumData[];
  /** 最初に見せる統合結果の名前 */
  readonly initial: string;
  /**
   * 案内の間の様子。あれば、枠を開くか（`open`）と選ぶ統合結果（`picked`）は画面の中の選びではなくこれに従う。
   * 案内の間の見本はキーを替えて別に作るので、閉じたあとの実際の枠には持ち越さない
   */
  readonly tour?: { readonly open: boolean; readonly picked: string };
}

export function Sum({ sums, initial, tour }: SumProps): JSX.Element | null {
  const [picked, setPicked] = useState(initial);
  if (sums.length === 0) {
    return null;
  }
  const sum = sums.find((s) => s.name === (tour === undefined ? picked : tour.picked)) ?? sums[0];
  const used = sum.templates.filter((row) => row.effective).length;
  return (
    <details className="sum" id="sum" data-tour="sum" open={tour === undefined ? undefined : tour.open}>
      <summary>統合結果（読み取り専用）</summary>
      <p className="hint">
        共通の設定に選んだ設定を重ねた、実行ファイルの答えをそのまま並べた結果です。中身を確認するときは、画面上部の「設定」の欄で見たい設定に切り替えます。
        同じ名前が重なって破棄された側には、その理由を出します。
      </p>
      <label className="sum-pick" data-tour="sum-pick">
        統合結果{" "}
        <select id="sum-select" value={sum.name} onChange={(event) => setPicked(event.target.value)}>
          {sums.map((s) => (
            <option key={s.name} value={s.name}>
              {sumLabel(s.name)}
            </option>
          ))}
        </select>
      </label>
      <p className="count" data-count="sum" data-tour="sum-count">
        使われるのは {used} 件（{layersText(sum.layers)}）
      </p>
      {sum.templates.length === 0 ? (
        <p className="empty">テンプレートがありません</p>
      ) : (
        <div className="table-scroll" data-tour="sum-table">
          <table className="sum-table">
            <thead>
              <tr>
                <th>名前</th>
                <th>由来</th>
                <th>状態</th>
                <th>破棄した理由</th>
              </tr>
            </thead>
            <tbody>
              {sum.templates.map((row, index) => (
                <tr key={`${row.layer}/${row.name}-${index}`} data-name={row.name} data-layer={row.layer}>
                  <td>
                    <code>{row.name}</code>
                  </td>
                  <td>{layerLabel(row.layer)}</td>
                  <td>{row.effective ? <span className="tag ok">使われる</span> : <span className="tag">使われない</span>}</td>
                  <td className="why">{row.dropped === null ? "" : `${droppedLabel(row.dropped.reason)}: ${row.dropped.detail}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
