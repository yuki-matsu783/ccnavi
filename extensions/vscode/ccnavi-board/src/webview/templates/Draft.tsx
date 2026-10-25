/**
 * 下書き 1 件（`wip/handoff/` に置かれたテンプレートの下書き）。読むだけの行で、取り込みも編集もここでは行わない。
 * 効力は無い下書きだとバッジで示し、検査の指摘と計画を見せる。取り込みは「取り込み待ち」の画面で行う。
 */
import type { JSX } from "react";

import type { DraftEntry } from "../../core/templates-view.js";
import { Flows } from "./Template.js";
import { Plan } from "./Plan.js";
import { Problems } from "./Problems.js";
import { isTodo, treeLabel } from "./text.js";

export interface DraftProps {
  readonly draft: DraftEntry;
  readonly open: boolean;
  readonly onToggle: () => void;
  /** 案内が指す先にする（一覧の最初の 1 件だけ） */
  readonly tour: boolean;
}

export function Draft({ draft, open, onToggle, tour }: DraftProps): JSX.Element {
  const todo = draft.problems.some(isTodo);
  const bad = draft.problems.some((problem) => problem.severity === "error");
  return (
    <li className={`row draft${open ? " open" : ""}`} data-name={draft.name} data-tour={tour ? "draft" : undefined}>
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
        <span className="tpl-name mono">{draft.name}</span>
        <span className="tag">{treeLabel(draft.tree)}</span>
        {bad && <span className="tag bad">error あり</span>}
        {todo && <span className="tag warn">TODO が残っています</span>}
        {!draft.valid && <span className="tag bad">読み込めません</span>}
      </div>
      {open && (
        <div className="row-body draft-body">
          <p className="hint mono small">{draft.path}</p>
          {draft.description !== "" && (
            <p>
              <span className="dim">説明</span> {draft.description}
            </p>
          )}
          {draft.when !== "" && (
            <p>
              <span className="dim">使う場面</span> {draft.when}
            </p>
          )}
          <Problems problems={draft.problems} />
          <Plan title="全体計画" items={draft.plan} />
          <Plan title="フィードバック計画" items={draft.feedback} />
          <Flows flows={draft.flows} hasFlows={draft.has_flows} />
        </div>
      )}
    </li>
  );
}
