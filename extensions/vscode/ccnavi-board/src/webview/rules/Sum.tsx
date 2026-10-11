/**
 * 「足し算」（読み取り専用）。適用されているルールは共通の設定との和なので、ワークスペースの足し算（共通の設定 + ワークスペースの設定）と、
 * プロジェクトの足し算（共通の設定 + そのプロジェクトの設定）を、実行ファイルが出した結果のまま並べる。
 *
 * ここは見せるだけ。判定も合成もせず、直すのは上の編集する 1 本（画面上部の「設定」の欄で切り替える）。
 * 保存済みの内容の和で、編集中の内容は含まない。
 */
import { useState, type JSX } from "react";

import { SECTION_LABELS, SECTIONS } from "../../core/rules-view.js";
import type { RulesSum } from "../../core/sums.js";

export interface SumProps {
  readonly sums: readonly RulesSum[];
  /** 最初に見せる足し算の名前 */
  readonly initial: string;
}

export function Sum({ sums, initial }: SumProps): JSX.Element | null {
  const [picked, setPicked] = useState(initial);
  if (sums.length === 0) {
    return null;
  }
  const sum = sums.find((s) => s.name === picked) ?? sums[0];
  const total = SECTIONS.reduce((n, section) => n + sum[section].length, 0);
  return (
    <details className="sum" id="sum">
      <summary>足し算（読み取り専用。共通の設定 + 1 つの設定の和）</summary>
      <p className="hint">
        判定に効くルールは、共通の設定と、その設定のルールの和です。ここは実行ファイルが出した結果をそのまま見せるだけで、直すときは画面上部の「設定」の欄で編集する 1 本に切り替えます。保存済みの内容の和で、編集中の内容は含みません。
        パスを持たないツール（Bash など）が当てるのは、全プロジェクトの設定を足した和ではありません。
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
      {sum.unreadable !== "" && (
        <div className="banner warn">実行ファイルは {sum.path} を読めず、その設定を空として扱っています（共通の設定のルールだけが効いています）: {sum.unreadable}</div>
      )}
      {sum.missing && sum.unreadable === "" && <p className="hint">{sum.path} は無いので、共通の設定のルールだけの和です（無いのは「設定が無い」正常な状態です）。</p>}
      <p className="count" data-count="sum">
        {total} 件（{sum.layers.join(" + ")}）
      </p>
      {SECTIONS.map((section) => (
        <section className="sum-section" data-section={section} key={section}>
          <h3>
            <span className={`section-name ${section}`}>{section}</span> <span className="section-label">{SECTION_LABELS[section]}</span>{" "}
            <span className="count">{sum[section].length} 件</span>
          </h3>
          {sum[section].length === 0 ? (
            <p className="empty">ルールがありません</p>
          ) : (
            <div className="table-scroll">
              <table className="sum-table">
                <thead>
                  <tr>
                    <th>id</th>
                    <th>ツール</th>
                    <th>パターン</th>
                    <th>由来</th>
                    <th>メッセージ</th>
                  </tr>
                </thead>
                <tbody>
                  {sum[section].map((rule, index) => (
                    <tr key={`${rule.id}-${index}`}>
                      <td>
                        <code>{rule.id}</code>
                      </td>
                      <td>
                        <code>{rule.match}</code>
                      </td>
                      <td>
                        <span className="dim">{rule.kind}</span> <code title={rule.pattern === "" ? undefined : `翻訳後の正規表現: ${rule.pattern}`}>{rule.written}</code>
                      </td>
                      <td>{rule.source === "common" ? "共通の設定" : rule.source === "self" ? "ワークスペース" : rule.source}</td>
                      <td className="why">{rule.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
    </details>
  );
}
