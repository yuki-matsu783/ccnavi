/**
 * 「統合結果」（読み取り専用）。判定は共通の設定と、親の `project:` が指す設定の和で行うので、ワークスペースの統合結果
 * （共通の設定 + ワークスペースの設定）と、プロジェクトの統合結果（共通の設定 + そのプロジェクトの設定）を、
 * 実行ファイルが出した結果のまま並べる。
 *
 * ここは見せるだけ。点も合成も数えず、直すのは上の編集する 1 本（画面上部の「設定」の欄で切り替える）。
 * 保存済みの配点の和で、編集中の内容は含まない。
 */
import { useState, type JSX } from "react";

import { LEVEL_NAMES } from "../../core/risk-view.js";
import { layerLabel, layersText, type RiskSum } from "../../core/sums.js";

export interface SumProps {
  readonly sums: readonly RiskSum[];
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
  return (
    <details className="sum" id="sum" data-tour="sum" open={tour === undefined ? undefined : tour.open}>
      <summary>統合結果（読み取り専用）</summary>
      <p className="hint">
        共通の設定に選んだ設定を重ねた、実行ファイルの答えをそのまま並べた結果です。重ねるのは、親の <code>project:</code> が指す設定だけです。保存済みの配点が対象で、編集中の内容は含みません。直すときは、画面上部の「設定」の欄で編集する設定に切り替えます。
      </p>
      <label className="sum-pick" data-tour="sum-pick">
        統合結果{" "}
        <select id="sum-select" value={sum.name} onChange={(event) => setPicked(event.target.value)}>
          {sums.map((s) => (
            <option key={s.name} value={s.name}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      {sum.fallback !== "" && <div className="banner warn">{sum.fallback}</div>}
      {sum.problems.length > 0 && (
        <ul className="problems">
          {sum.problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
        </ul>
      )}
      <p className="sum-levels" id="sum-levels" data-tour="sum-levels">
        リスクレベルの境目の点:{" "}
        {LEVEL_NAMES.map((name) => (
          <span key={name} className="sum-level">
            {name.toUpperCase()} <strong>{sum.levels[name] ?? "-"}</strong>
          </span>
        ))}
      </p>
      <p className="count" data-count="sum">
        項目 {sum.factors.length} 件（{layersText(sum.layers)}）
      </p>
      {sum.factors.length === 0 ? (
        <p className="empty">項目がありません。加点する項目がなければ、どの子も LOW のまま閉じられます</p>
      ) : (
        <div className="table-scroll" data-tour="sum-table">
          <table className="sum-table">
            <thead>
              <tr>
                <th>id</th>
                <th>加点条件</th>
                <th>点</th>
                <th>由来</th>
                <th>理由</th>
              </tr>
            </thead>
            <tbody>
              {sum.factors.map((factor, index) => (
                <tr key={`${factor.id}-${index}`}>
                  <td>
                    <code>{factor.id}</code>
                  </td>
                  <td>
                    <span className="dim">{factor.kind}</span> <code>{factor.value}</code>
                  </td>
                  <td className="num">{factor.points ?? ""}</td>
                  <td>{layerLabel(factor.source)}</td>
                  <td className="why">{factor.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
