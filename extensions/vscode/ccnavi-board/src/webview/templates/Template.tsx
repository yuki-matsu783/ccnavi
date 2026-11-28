/**
 * テンプレート1件。見出しに名前・設定の名前・状態のバッジ・説明を出し、開くと計画とフローの図・本文の取り出しを出す。
 * 判定（有効・無効・使われない・破棄した理由・読めない）は実行ファイルの答えのまま出す。
 */
import { useEffect, type JSX } from "react";

import { revealControls } from "../../core/templatesmodel.js";
import type { FlowSummary, TemplateCheck, TemplateDetail, TemplateEntry } from "../../core/templates-view.js";
import { Diagrams } from "./Diagrams.js";
import { Plan } from "./Plan.js";
import { Problems } from "./Problems.js";
import { droppedLabel, flowText, layerLabel } from "./text.js";

export interface TemplateProps {
  readonly entry: TemplateEntry;
  readonly open: boolean;
  readonly onToggle: () => void;
  /** 本文の答え。まだ読んでいなければundefined */
  readonly detail: TemplateDetail | undefined;
  /** 本文を読めなかったときの理由。無ければ空 */
  readonly detailError: string;
  readonly onShow: () => void;
  /** 案内が指す先にする（一覧の最初の1件だけ） */
  readonly tour: boolean;
  /** 編集を始める。渡さなければ「編集」は出さない（読み取り専用の設定・読めない行・案内の間） */
  readonly onEdit?: () => void;
  /** 図の中身（実行ファイルに読ませた答え）。まだ読んでいなければundefined。読めなかったときは`previewError`に理由 */
  readonly preview?: TemplateCheck | undefined;
  readonly previewError?: string;
  /** 開いたとき、図の中身を求める。渡さなければ求めない（案内の間） */
  readonly onPreview?: () => void;
}

export function Template({ entry, open, onToggle, detail, detailError, onShow, tour, onEdit, preview, previewError = "", onPreview }: TemplateProps): JSX.Element {
  const wantPreview = open && preview === undefined && previewError === "" ? onPreview : undefined;
  useEffect(() => {
    wantPreview?.();
  }, [wantPreview]);
  return (
    <li className={`row template${open ? " open" : ""}`} data-name={entry.name} data-layer={entry.layer} data-tour={tour ? "template" : undefined}>
      <div
        className="row-head"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <span className="twist">{open ? "▾" : "▸"}</span>
        <span className="tpl-name mono">{entry.name}</span>
        <span className="tag tpl-layer">{layerLabel(entry.layer)}</span>
        <StateTags entry={entry} />
        {entry.description !== "" && <span className="tpl-desc">{entry.description}</span>}
      </div>
      {open && (
        <div className="row-body tpl-body">
          {entry.when !== "" && (
            <p>
              <span className="dim">使う場面</span> {entry.when}
            </p>
          )}
          <p className="hint mono small">{entry.path}</p>
          <Problems problems={entry.problems} />
          {preview !== undefined ? (
            <Diagrams check={preview} />
          ) : previewError !== "" ? (
            <p className="status failed" data-role="preview-error">
              図を出せません: {previewError}
            </p>
          ) : (
            <>
              <Plan title="全体計画" items={entry.plan} />
              <Plan title="フィードバック計画" items={entry.feedback} />
              <Flows flows={entry.flows} hasFlows={entry.has_flows} />
            </>
          )}
          <button type="button" className="action small" data-action="show-text" onClick={onShow}>
            本文を見る
          </button>
          {onEdit !== undefined && (
            <button type="button" className="action small" data-action="edit-template" onClick={onEdit}>
              編集
            </button>
          )}
          {detail !== undefined && detailMatches(detail, entry) && (
            <>
              <h3 className="tpl-text-head">本文（制御文字・書式文字は\uXXXXで表示）</h3>
              <pre className="tpl-text" data-role="template-text">
                {revealControls(detail.text)}
              </pre>
            </>
          )}
          {detail !== undefined && !detailMatches(detail, entry) && (
            <p className="hint" data-role="detail-mismatch">
              このテンプレートの本文は、この画面では表示できません（同じ名前のテンプレートが別の設定にあり、ccnaviはそちらの本文を返すため）。次のファイルを直接開いて確認してください。<span className="mono">{entry.path}</span>
            </p>
          )}
          {detailError !== "" && <p className="status failed">{detailError}</p>}
        </div>
      )}
    </li>
  );
}

/**
 * 届いた本文が、この行のものか。実行ファイルは設定ごとでなく統合結果で1件を返すので、破棄された行を開くと
 * 別の設定の本文が来ることがある。設定とsha（読めない行はshaが空なので設定だけ）で突き合わせる
 */
function detailMatches(detail: TemplateDetail, entry: TemplateEntry): boolean {
  return detail.layer === entry.layer && (entry.sha === "" || detail.sha === entry.sha);
}

/**
 * 状態のバッジ。有効・無効・使われない・破棄した理由・読み取り専用・読めない。色と文字の両方で出す。
 * 共通と全欄が同じで破棄した行（`duplicate`）は、共通の1本が使われていて直すことは無いので、無効や破棄の色を付けず「共通の側を使用」とだけ出す
 */
function StateTags({ entry }: { readonly entry: TemplateEntry }): JSX.Element {
  return (
    <>
      {entry.dropped?.reason === "duplicate" ? (
        <span className="tag">共通設定の側を使用</span>
      ) : entry.dropped !== null ? (
        <>
          {!entry.usable && <span className="tag bad">無効</span>}
          <span className="tag warn">{`破棄: ${droppedLabel(entry.dropped.reason)}`}</span>
        </>
      ) : (
        <>
          {entry.usable ? <span className="tag ok">有効</span> : <span className="tag bad">無効</span>}
          {!entry.effective && <span className="tag">使われない</span>}
        </>
      )}
      {!entry.writable && <span className="tag">読み取り専用</span>}
      {!entry.valid && <span className="tag bad">読み込めません</span>}
    </>
  );
}

/** フローの要約。フローが無くて`has_flows`が偽なら、無いと言う */
export function Flows({ flows, hasFlows }: { readonly flows: readonly FlowSummary[]; readonly hasFlows: boolean }): JSX.Element | null {
  if (flows.length > 0) {
    return (
      <ul className="tpl-flows">
        {flows.map((flow) => (
          <li key={flow.index}>{flowText(flow)}</li>
        ))}
      </ul>
    );
  }
  if (!hasFlows) {
    return <p className="hint">子のフローはありません</p>;
  }
  return null;
}
