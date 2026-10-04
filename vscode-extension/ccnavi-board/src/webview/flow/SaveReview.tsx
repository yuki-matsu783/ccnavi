/**
 * 保存の前に見せる差分の一覧（`core/flow-diff.ts` の `diffFlows`）。未保存なのに差分が空なら、順序だけが変わった
 * （`sameFlow` は配列の順を見る）と言う。読み込んだ時点から、足した・消した・変えた
 * ノードと線を並べ、「保存する」で拡張ホストへ保存を頼む。「やめる」か Esc で閉じる（編集はそのまま）。
 *
 * 「次から確かめずに保存する」にチェックを付けて保存すると、設定 `ccnaviBoard.flowSaveReview` を外す（呼び手が送る）。
 * 見た目は `SaveReview.css`。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import { isEmptyDiff, type ConnectionChange, type FlowDiff, type NodeChange } from "../../core/flow-diff.js";

function Items({ title, kind, items }: { readonly title: string; readonly kind: string; readonly items: readonly (NodeChange | ConnectionChange)[] }): JSX.Element | null {
  if (items.length === 0) {
    return null;
  }
  return (
    <>
      <h3>
        {title}（{items.length}）
      </h3>
      <ul className="review-list" data-kind={kind}>
        {items.map((item, index) => (
          <li key={index}>
            {item.label}
            {item.fields.length > 0 && <span className="dim">: {item.fields.join("・")}</span>}
          </li>
        ))}
      </ul>
    </>
  );
}

export interface SaveReviewProps {
  readonly diff: FlowDiff;
  /** 保存する。`skipNext` が真なら、次から確かめない */
  readonly onConfirm: (skipNext: boolean) => void;
  readonly onCancel: () => void;
}

export function SaveReview({ diff, onConfirm, onCancel }: SaveReviewProps): JSX.Element {
  const [skip, setSkip] = useState(false);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirm.current?.focus();
  }, []);
  return (
    <div className="review-backdrop">
      <section className="review" id="save-review" role="dialog" aria-modal="true" aria-labelledby="save-review-title">
        <h2 id="save-review-title">保存する前に変更を確かめる</h2>
        <p className="dim small">読み込んだ時点からの変更です。</p>
        {isEmptyDiff(diff) && (
          <p id="review-order-only">順序だけが変わりました（ノードと線の中身は同じで、ファイルに書く順が変わります）。</p>
        )}
        {diff.meta.length > 0 && (
          <>
            <h3>フロー</h3>
            <ul className="review-list" data-kind="meta">
              <li>{diff.meta.join("・")}</li>
            </ul>
          </>
        )}
        <Items title="足したノード" kind="added-nodes" items={diff.addedNodes} />
        <Items title="消したノード" kind="removed-nodes" items={diff.removedNodes} />
        <Items title="変えたノード" kind="changed-nodes" items={diff.changedNodes} />
        <Items title="足した線" kind="added-connections" items={diff.addedConnections} />
        <Items title="消した線" kind="removed-connections" items={diff.removedConnections} />
        <Items title="変えた線" kind="changed-connections" items={diff.changedConnections} />
        <label className="review-skip">
          <input type="checkbox" className="f-review-skip" checked={skip} onChange={(event) => setSkip(event.target.checked)} />
          次から確かめずに保存する（設定 ccnaviBoard.flowSaveReview で戻せます）
        </label>
        <div className="review-actions">
          <button type="button" className="action" data-action="cancel-save" onClick={onCancel}>
            やめる
          </button>
          <button type="button" className="action primary" data-action="confirm-save" ref={confirm} onClick={() => onConfirm(skip)}>
            保存する
          </button>
        </div>
      </section>
    </div>
  );
}
