/**
 * テンプレート 1 件。見出しに名前・層・状態の札・説明を出し、開くと計画・フロー・本文の取り出しを出す。
 * 判定（使える・使われない・捨てた理由・読めない）は実行ファイルの答えのまま出す。
 */
import type { JSX } from "react";

import { revealControls } from "../../core/templatesmodel.js";
import type { EditOp, FlowSummary, TemplateDetail, TemplateEntry } from "../../core/templates-view.js";
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
  /** 編集を使えるか（実行ファイルが持つか）。使えなければ理由 */
  readonly editEnabled: boolean;
  readonly editReason: string;
  /** 編集の欄を開く（押した操作） */
  readonly onEdit: (op: EditOp) => void;
  /** 開いている編集の欄。この行のものでなければ null */
  readonly editPanel: JSX.Element | null;
}

/** 書ける（共通かプロジェクトの層にある）テンプレートの見出しに出す編集のボタン */
const EDIT_BUTTONS: readonly { readonly op: EditOp; readonly label: string; readonly action: string }[] = [
  { op: "rename", label: "改名", action: "rename-template" },
  { op: "move", label: "移動", action: "move-template" },
  { op: "delete", label: "削除", action: "delete-template" },
];

export function Template({ entry, open, onToggle, detail, detailError, onShow, tour, editEnabled, editReason, onEdit, editPanel }: TemplateProps): JSX.Element {
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
        {entry.writable &&
          EDIT_BUTTONS.map((button) => (
            <button
              key={button.action}
              type="button"
              className="action small"
              data-action={button.action}
              disabled={!editEnabled}
              title={!editEnabled ? editReason : undefined}
              onClick={(event) => {
                event.stopPropagation();
                onEdit(button.op);
              }}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {button.label}
            </button>
          ))}
      </div>
      {editPanel}
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
              この行の本文は読み込めません。実行ファイルは、足し算で使われる同名の別の層の本文を返すためです（捨てられた行など）。ファイルを直接開いて読んでください: <span className="mono">{entry.path}</span>
            </p>
          )}
          {detailError !== "" && <p className="status failed">{detailError}</p>}
        </div>
      )}
    </li>
  );
}

/**
 * 届いた本文が、この行のものか。実行ファイルは層ごとでなく足し算で 1 件を返すので、捨てられた行を開くと
 * 別の層の本文が来ることがある。層と sha（読めない行は sha が空なので層だけ）で突き合わせる
 */
function detailMatches(detail: TemplateDetail, entry: TemplateEntry): boolean {
  return detail.layer === entry.layer && (entry.sha === "" || detail.sha === entry.sha);
}

/** 状態の札。使える・使われない・捨てた理由・読み取り専用・読めない。色と文字の両方で出す */
function StateTags({ entry }: { readonly entry: TemplateEntry }): JSX.Element {
  return (
    <>
      {entry.usable ? <span className="tag ok">使える</span> : <span className="tag bad">使えない</span>}
      {!entry.effective && <span className="tag">使われない</span>}
      {entry.dropped !== null && <span className="tag warn">{`捨てた: ${droppedLabel(entry.dropped.reason)}`}</span>}
      {!entry.writable && <span className="tag">読み取り専用</span>}
      {!entry.valid && <span className="tag bad">読み込めない</span>}
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
