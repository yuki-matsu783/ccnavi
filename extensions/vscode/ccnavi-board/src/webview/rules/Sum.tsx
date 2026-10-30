/**
 * 「統合結果」（読み取り専用）。適用されているルールは共通の設定との和なので、統合結果（共通 + ワークスペース）と、
 * プロジェクトの統合結果（共通の設定 + そのプロジェクトの設定）を、実行ファイルが出した結果のまま並べる。
 *
 * ここは見せるだけ。直すのは上の編集する 1 本（画面上部の「設定」の欄で切り替える）。
 * 保存済みの内容の和で、編集中の内容は含まない。
 */
import { useState, type JSX } from "react";

import { SECTION_LABELS, SECTIONS } from "../../core/rules-view.js";
import { layerLabel, layersText, type RulesSum } from "../../core/sums.js";

export interface SumProps {
  readonly sums: readonly RulesSum[];
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
  const total = SECTIONS.reduce((n, section) => n + sum[section].length, 0);
  return (
    <details className="sum" id="sum" data-tour="sum" open={tour === undefined ? undefined : tour.open}>
      <summary>統合結果（読み取り専用）</summary>
      <p className="hint">
        共通の設定に選んだ設定を重ねた、実行ファイルの答えをそのまま並べた結果です。保存済みの内容が対象で、編集中の内容は含みません。直すときは、画面上部の「設定」の欄で編集する設定に切り替えます。
        パスを持たないツール（Bash など）に効くのは、全プロジェクトの設定を合わせたものではありません。
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
      {sum.unreadable !== "" && (
        <div className="banner warn">実行ファイルは {sum.path} を読み込めず、その設定を空として扱っています（共通の設定のルールだけが効いています）: {sum.unreadable}</div>
      )}
      {sum.missing && sum.unreadable === "" && <p className="hint">{sum.path} はないので、共通の設定のルールだけです（ファイルがないのは、その設定がまだ作られていない正常な状態です）。</p>}
      <p className="count" data-count="sum" data-tour="sum-count">
        {total} 件（{layersText(sum.layers)}）
      </p>
      {SECTIONS.map((section) => (
        <section className="sum-section" data-section={section} data-tour={`sum-${section}`} key={section}>
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
                      <td>{layerLabel(rule.source)}</td>
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
