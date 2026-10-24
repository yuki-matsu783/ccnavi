/**
 * テンプレート 1 件。見出しに名前・層・状態の札・説明を出し、最初から開いていて、計画とフローの図・本文の取り出しを出す（畳める）。
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
  /** 本文の答え。まだ読んでいなければ undefined */
  readonly detail: TemplateDetail | undefined;
  /** 本文を読めなかったときの理由。無ければ空 */
  readonly detailError: string;
  readonly onShow: () => void;
  /** 案内が指す先にする（一覧の最初の 1 件だけ） */
  readonly tour: boolean;
  /** 編集を始める。渡さなければ「図を編集」は出さない（読み取り専用の層・読めない行・案内の間） */
  readonly onEdit?: () => void;
  /** 図の中身（実行ファイルに読ませた答え）。まだ読んでいなければ undefined。読めなかったときは `previewError` に理由 */
  readonly preview?: TemplateCheck | undefined;
  readonly previewError?: string;
  /** 開いたとき、図の中身を求める。渡さなければ求めない（案内の間） */
  readonly onPreview?: () => void;
}

export function Template({ entry, open, onToggle, detail, detailError, onShow, tour, onEdit, preview, previewError = "", onPreview }: TemplateProps): JSX.Element {
  const dropped = entry.dropped;
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
      {dropped !== null && <p className="hint tpl-dropped">{dropped.detail}</p>}
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
              図を編集
            </button>
          )}
          {detail !== undefined && detailMatches(detail, entry) && (
            <>
              <h3 className="tpl-text-head">本文（制御文字・書式文字は \uXXXX で表示）</h3>
              <pre className="tpl-text" data-role="template-text">
                {revealControls(detail.text)}
              </pre>
            </>
          )}
          {detail !== undefined && !detailMatches(detail, entry) && (
            <p className="hint" data-role="detail-mismatch">
              この行の本文は読み込めません。実行ファイルは、統合結果で使われる同名の別の層の本文を返すためです（破棄された行など）。ファイルを直接開いて読んでください: <span className="mono">{entry.path}</span>
            </p>
          )}
          {detailError !== "" && <p className="status failed">{detailError}</p>}
        </div>
      )}
    </li>
  );
}

/**
 * 届いた本文が、この行のものか。実行ファイルは層ごとでなく統合結果で 1 件を返すので、破棄された行を開くと
 * 別の層の本文が来ることがある。層と sha（読めない行は sha が空なので層だけ）で突き合わせる
 */
function detailMatches(detail: TemplateDetail, entry: TemplateEntry): boolean {
  return detail.layer === entry.layer && (entry.sha === "" || detail.sha === entry.sha);
}

/** 状態の札。有効・無効・使われない・破棄した理由・読み取り専用・読めない。色と文字の両方で出す */
function StateTags({ entry }: { readonly entry: TemplateEntry }): JSX.Element {
  return (
    <>
      {entry.usable ? <span className="tag ok">有効</span> : <span className="tag bad">無効</span>}
      {!entry.effective && <span className="tag">使われない</span>}
      {entry.dropped !== null && <span className="tag warn">{`破棄した: ${droppedLabel(entry.dropped.reason)}`}</span>}
      {!entry.writable && <span className="tag">読み取り専用</span>}
      {!entry.valid && <span className="tag bad">読み込めません</span>}
    </>
  );
}

/** フローの要約。フローが無くて `has_flows` が偽なら、無いと言う */
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
