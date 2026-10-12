/**
 * 下書き 1 件。見出しに「取り込む…」を持ち、その直下に取り込みの欄（`ImportForm.tsx`）を出す。
 * 欄が開いていれば、下見の答え（`Preview.tsx`）も欄の下に出す。効力は無い下書きだと札で示す。
 * 取り込めるかは実行ファイルの答え（`importable`）のまま使う。
 */
import type { JSX } from "react";

import type { Lock } from "../../core/lock.js";
import type { DraftEntry } from "../../core/templates-view.js";
import { Flows } from "./Template.js";
import { ImportForm, type Importing } from "./ImportForm.js";
import { Plan } from "./Plan.js";
import { Preview, type PreviewState } from "./Preview.js";
import { Problems } from "./Problems.js";
import { isTodo, layerLabel, treeLabel } from "./text.js";

/** 同名が別のツリーにあるときの「取り込む…」の説明 */
const AMBIGUOUS_TITLE = "同名の下書きが別のツリーにあり、名前では指せません。ccnavi --templates --import にパスを渡してください";

export interface DraftProps {
  readonly draft: DraftEntry;
  readonly open: boolean;
  readonly onToggle: () => void;
  /** 案内が指す先にする（一覧の最初の 1 件だけ） */
  readonly tour: boolean;
  /** 案内が「取り込む…」を指す先にする（取り込める最初の 1 件。取り込める下書きが無ければ、どれも false） */
  readonly importTour: boolean;
  /** 取り込みの欄の状態。開いているのは 1 件だけで、この下書きのものでなければ別の下書きが開いている */
  readonly importing: Importing | undefined;
  /** 同じ名前の下書きが別のツリーにもある。名前では取り込み先を指せないので、取り込めない */
  readonly ambiguous: boolean;
  /** 取り込み先に選べる層（common を除く） */
  readonly importLayers: readonly string[];
  readonly onOpenImport: () => void;
  readonly onCloseImport: () => void;
  readonly onImportChange: (next: Importing) => void;
  readonly onPreview: () => void;
  readonly preview: PreviewState | undefined;
  readonly onClosePreview: () => void;
  /** 下見・取り込みの往復の途中か */
  readonly importBusy: boolean;
  /** プレビューに渡す錠（取り込み先に合わせて App が選ぶ） */
  readonly lock: Lock;
  readonly onImport: (expect: string) => void;
  /** 案内の間か（案内の間は下見も取り込みも頼めない） */
  readonly touring: boolean;
}

export function Draft({
  draft,
  open,
  onToggle,
  tour,
  importTour,
  importing,
  ambiguous,
  importLayers,
  onOpenImport,
  onCloseImport,
  onImportChange,
  onPreview,
  preview,
  onClosePreview,
  importBusy,
  lock,
  onImport,
  touring,
}: DraftProps): JSX.Element {
  const todo = draft.problems.some(isTodo);
  /** この下書きの取り込みの欄（開いていなければ undefined） */
  const mine = importing !== undefined && importing.draft === draft.name ? importing : undefined;
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
        {draft.importable ? <span className="tag ok">取り込める</span> : <span className="tag bad">取り込めない</span>}
        {draft.exists_in.length > 0 && <span className="tag warn">{`同名あり: ${draft.exists_in.join(", ")}（取り込むと置き換え）`}</span>}
        {draft.also_in.length > 0 && <span className="tag">{`ほかのツリーにも同じ下書き: ${draft.also_in.map((tree) => treeLabel(tree)).join(", ")}`}</span>}
        {todo && <span className="tag warn">TODO が残っている</span>}
        {ambiguous && <span className="tag bad">同名が別のツリーにもある</span>}
        {/* 見出しの開閉と混ざらないよう、このボタンの操作は見出しに伝えない（キーの Enter / Space も同じ） */}
        <button
          type="button"
          className="action small import-open"
          data-action="open-import"
          data-tour={importTour ? "draft-import" : undefined}
          disabled={!draft.importable || ambiguous}
          title={!draft.importable ? "検査で error があるので取り込めません" : ambiguous ? AMBIGUOUS_TITLE : undefined}
          onClick={(event) => {
            event.stopPropagation();
            onOpenImport();
          }}
          onKeyDown={(event) => event.stopPropagation()}
        >
          取り込む…
        </button>
      </div>
      {mine !== undefined && (
        <ImportForm
          draft={draft}
          importing={mine}
          layers={importLayers}
          busy={importBusy}
          touring={touring}
          onChange={onImportChange}
          onPreview={onPreview}
          onClose={onCloseImport}
        />
      )}
      {mine !== undefined && preview !== undefined && (
        <Preview
          request={preview.request}
          answer={preview.answer}
          error={preview.error}
          lock={lock}
          busy={importBusy || touring}
          onImport={onImport}
          onClose={onClosePreview}
        />
      )}
      {open && (
        <div className="row-body draft-body">
          <p className="hint mono small">{draft.path}</p>
          <p>
            <span className="dim">検査に使った層</span> {layerLabel(draft.checked_with)}
          </p>
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
