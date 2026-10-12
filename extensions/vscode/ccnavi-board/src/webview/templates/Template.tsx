/**
 * テンプレート 1 件。見出しに名前・層・状態の札・説明を出し、開くと計画・フロー・本文の取り出しを出す。
 * 判定（使える・使われない・捨てた理由・読めない）は実行ファイルの答えのまま出す。
 */
import type { JSX } from "react";

import { revealControls } from "../../core/templatesmodel.js";
import type { FlowSummary, TemplateDetail, TemplateEntry } from "../../core/templates-view.js";
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
}

export function Template({ entry, open, onToggle, detail, detailError, onShow, tour }: TemplateProps): JSX.Element {
  const dropped = entry.dropped;
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
          <Plan title="全体計画" items={entry.plan} />
          <Plan title="フィードバック計画" items={entry.feedback} />
          <Flows flows={entry.flows} hasFlows={entry.has_flows} />
          <button type="button" className="action small" data-action="show-text" onClick={onShow}>
            本文を見る
          </button>
          {detail !== undefined && (
            <>
              <h3 className="tpl-text-head">本文（制御文字・書式文字は \uXXXX で表示）</h3>
              <pre className="tpl-text" data-role="template-text">
                {revealControls(detail.text)}
              </pre>
            </>
          )}
          {detailError !== "" && <p className="status failed">{detailError}</p>}
        </div>
      )}
    </li>
  );
}

/** 状態の札。使える・使われない・捨てた理由・読み取り専用・読めない。色と文字の両方で出す */
function StateTags({ entry }: { readonly entry: TemplateEntry }): JSX.Element {
  return (
    <>
      {entry.usable ? <span className="tag ok">使える</span> : <span className="tag bad">使えない</span>}
      {!entry.effective && <span className="tag">使われない</span>}
      {entry.dropped !== null && <span className="tag warn">{`捨てた: ${droppedLabel(entry.dropped.reason)}`}</span>}
      {!entry.writable && <span className="tag">読み取り専用</span>}
      {!entry.valid && <span className="tag bad">読めない</span>}
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
