/**
 * 承認のオーバーレイと対応方針の画面の目次。押すと、同じ1本のページの中のその位置まで送るだけで、中身を分けて出すものではない
 * （承認の本文は実行ファイルが組んだ1本のまま。承認のダイジェストも表示した一覧全体に対して作られる）。
 * 件数が0の項目は押せない（行き先が無い）。
 */
import type { JSX } from "react";

export interface TocItem {
  /** 行き先の要素の`id` */
  readonly target: string;
  readonly label: string;
  /** 件数を添える項目だけ。0なら押せない */
  readonly count?: number;
}

export function Toc({ items, label }: { readonly items: readonly TocItem[]; readonly label: string }): JSX.Element {
  return (
    <nav className="overlay-toc" aria-label={label} data-toc>
      <div className="overlay-toc-head">目次</div>
      <ul>
        {items.map((item) => (
          <li key={item.target}>
            <button
              type="button"
              className="overlay-toc-item"
              data-toc-target={item.target}
              disabled={item.count === 0}
              onClick={() => document.getElementById(item.target)?.scrollIntoView?.({ block: "start", behavior: "smooth" })}
            >
              <span className="overlay-toc-label">{item.label}</span>
              {item.count === undefined ? null : <span className="overlay-toc-count">{item.count}</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
