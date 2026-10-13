/**
 * 取り込みの欄。下書き 1 件の見出しの直下に出し、取り込み先と置き換えを選んで下見を頼む。
 * 取り込む操作は持たない（下見の答えを見せて取り込むのは `Preview.tsx` の側）。
 *
 * 取り込み先の選び方と、置き換えの既定の決め方もここに置く。どちらも画面が決める値だけで、有効かどうかは実行ファイルが答える。
 */
import type { JSX } from "react";

import type { DraftEntry, ImportRequest } from "../../core/templates-view.js";
import { layerLabel } from "./text.js";

/** 取り込み先の選び方の接頭辞。プロジェクトの層は `project:<名前>` と書く */
const PROJECT_PREFIX = "project:";

/** 取り込みの欄の状態。`to` は選んだ取り込み先（`common` か `project:<名前>`） */
export interface Importing {
  readonly draft: string;
  readonly to: string;
  readonly replace: boolean;
}

export interface ImportFormProps {
  readonly draft: DraftEntry;
  readonly importing: Importing;
  /** 取り込み先に選べる層の名前（common を除く） */
  readonly layers: readonly string[];
  /** 下見・取り込みの往復の途中か */
  readonly busy: boolean;
  /** 案内の間か（案内の間は下見を頼めない） */
  readonly touring: boolean;
  readonly onChange: (next: Importing) => void;
  readonly onPreview: () => void;
  readonly onClose: () => void;
}

/** 取り込み先の選び方の値。common は common、それ以外はプロジェクトの層 */
function toOf(layer: string): string {
  return layer === "common" ? "common" : `${PROJECT_PREFIX}${layer}`;
}

/** 置き換えの既定。選んだ取り込み先の層が、下書きの同名のある層に含まれるとき真 */
function replaceFor(draft: DraftEntry, to: string): boolean {
  const layer = to === "common" ? "common" : to.slice(PROJECT_PREFIX.length);
  return draft.exists_in.includes(layer);
}

/** 取り込みの欄を開いたときの既定。取り込み先は同名のある先頭の層（無ければ共通）、置き換えはその層に同名があるか */
export function defaultImporting(draft: DraftEntry): Importing {
  const first: string | undefined = draft.exists_in[0];
  const to = first === undefined ? "common" : toOf(first);
  return { draft: draft.name, to, replace: replaceFor(draft, to) };
}

/** 欄の状態から、下見と取り込みに送る要求を作る */
export function requestOf(importing: Importing): ImportRequest {
  if (importing.to === "common") {
    return { draft: importing.draft, to: "common", layer: "", replace: importing.replace };
  }
  return { draft: importing.draft, to: "project", layer: importing.to.slice(PROJECT_PREFIX.length), replace: importing.replace };
}

/** 二つの要求が同じか（古い往復の答えを見分けるため） */
export function sameRequest(a: ImportRequest, b: ImportRequest): boolean {
  return a.draft === b.draft && a.to === b.to && a.layer === b.layer && a.replace === b.replace;
}

/** 取り込み先が変わったときの欄。置き換えは新しい取り込み先の既定に合わせ直す */
export function changedTo(draft: DraftEntry, importing: Importing, to: string): Importing {
  return { draft: importing.draft, to, replace: replaceFor(draft, to) };
}

/** 取り込みの欄。錠は出さない（錠は `Preview.tsx` が出す） */
export function ImportForm({ draft, importing, layers, busy, touring, onChange, onPreview, onClose }: ImportFormProps): JSX.Element {
  return (
    <div className="import-form" data-role="import-form" data-draft={draft.name} data-tour="import-form">
      <label>
        取り込み先 <select id="import-to" data-action="import-to" value={importing.to} onChange={(event) => onChange(changedTo(draft, importing, event.currentTarget.value))}>
          <option value="common">共通（.ccnavi/config/common/templates）</option>
          {layers.map((layer) => (
            <option key={layer} value={toOf(layer)}>
              {layerLabel(layer)}の層
            </option>
          ))}
        </select>
      </label>
      <label>
        <input type="checkbox" id="import-replace" data-action="import-replace" checked={importing.replace} onChange={(event) => onChange({ ...importing, replace: event.currentTarget.checked })} /> 同名のテンプレートを置き換える
      </label>
      <button type="button" className="action small" data-action="preview-import" disabled={busy || touring} onClick={onPreview}>
        取り込み内容をプレビュー
      </button>
      <button type="button" className="action small" data-action="close-import" onClick={onClose}>
        閉じる
      </button>
    </div>
  );
}
