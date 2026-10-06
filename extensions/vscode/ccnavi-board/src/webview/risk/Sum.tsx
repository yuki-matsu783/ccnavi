/**
 * 「足し算」（読み取り専用）。判定は共通の設定と、親の `project:` が指す設定の和で行うので、ワークスペースの足し算
 * （共通の設定 + ワークスペースの設定）と、プロジェクトの足し算（共通の設定 + そのプロジェクトの設定）を、
 * 実行ファイルが出した結果のまま並べる。
 *
 * ここは見せるだけ。点も合成も数えず、直すのは上の編集する 1 本（画面上部の「設定」の欄で切り替える）。
 * 保存済みの配点の和で、編集中の内容は含まない。
 */
import { useState, type JSX } from "react";

import { LEVEL_NAMES } from "../../core/risk-view.js";
import type { RiskSum } from "../../core/sums.js";

export interface SumProps {
  readonly sums: readonly RiskSum[];
  /** 最初に見せる足し算の名前 */
  readonly initial: string;
}

export function Sum({ sums, initial }: SumProps): JSX.Element | null {
  const [picked, setPicked] = useState(initial);
  if (sums.length === 0) {
    return null;
  }
  const sum = sums.find((s) => s.name === picked) ?? sums[0];
  return (
    <details className="sum" id="sum">
      <summary>足し算（読み取り専用。共通の設定 + 1 つの設定の和）</summary>
      <p className="hint">
        子チケットを閉じるときの配点は、共通の設定と、親の <code>project:</code> が指す設定の和です。ここは実行ファイルが出した結果をそのまま見せるだけで、直すときは画面上部の「設定」の欄で編集する 1 本に切り替えます。保存済みの配点の和で、編集中の内容は含みません。
        パスを持たない判定は、全プロジェクトの設定を足した和ではありません（配点の合成は、親の <code>project:</code> が指す 1 つの設定だけを足します）。
      </p>
      <label className="sum-pick">
        足し算{" "}
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
      <p className="sum-levels" id="sum-levels">
        リスクレベルの境目の点:{" "}
        {LEVEL_NAMES.map((name) => (
          <span key={name} className="sum-level">
            {name.toUpperCase()} <strong>{sum.levels[name] ?? "-"}</strong>
          </span>
        ))}
      </p>
      <p className="count" data-count="sum">
        項目 {sum.factors.length} 件（{sum.layers.join(" + ")}）
      </p>
      {sum.factors.length === 0 ? (
        <p className="empty">項目がありません。加点する項目が無ければ、どの子も LOW のまま閉じます</p>
      ) : (
        <div className="table-scroll">
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
                  <td>{factor.source === "common" ? "共通の設定" : factor.source === "self" ? "ワークスペース" : factor.source}</td>
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
