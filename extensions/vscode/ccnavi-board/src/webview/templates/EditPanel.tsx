/**
 * 編集の欄。1 件の編集の中身（本文・新しい名前・移す先・消す確認）と、下見の答えを 1 つの部品で出す。
 * 下見と実行の往復は `App.tsx` が持つ。この部品は欄の入力を `onChange` で返し、下見と実行を頼むだけにする。
 *
 * 実行ファイルが返した文字列は文字としてそのまま出す（HTML として解釈しない）。差分の色は `Preview.tsx` と同じ。
 */
import type { JSX } from "react";

import type { EditAnswer, EditRequest } from "../../core/templates-view.js";
import { diffClass } from "./Preview.js";
import { Problems } from "./Problems.js";
import { layerLabel } from "./text.js";

/** 編集の欄の状態。`request` は欄の要求、`answer` は下見の答え（まだ頼んでいなければ null） */
export interface EditState {
  readonly request: EditRequest;
  /** 下見の答え。まだ頼んでいなければ null */
  readonly answer: EditAnswer | null;
  /** 下見・実行が読めなかった理由 */
  readonly error: string;
  /** 下見か実行の往復の途中か */
  readonly busy: boolean;
}

export interface EditPanelProps {
  readonly state: EditState;
  /** 移動の行き先に選べる層（common を除く） */
  readonly layers: readonly string[];
  /** 錠の理由。止めなければ空 */
  readonly lockReason: string;
  readonly touring: boolean;
  readonly onChange: (request: EditRequest) => void;
  readonly onPreview: () => void;
  readonly onExecute: (expect: string) => void;
  readonly onClose: () => void;
}

/** 実行ボタンの動詞。削除は下書きでも主語でも同じ言葉 */
const VERB: Readonly<Record<EditRequest["op"], string>> = {
  save: "保存",
  rename: "改名",
  move: "移動",
  delete: "削除",
  "delete-draft": "削除",
};

/** 移動の行き先の値の接頭辞。プロジェクトの層は `project:<名前>` と書く */
const PROJECT_PREFIX = "project:";

/** 下見を頼めるか。入力が空のままでは頼まない（空の要求を実行ファイルに送らない） */
function filled(request: EditRequest): boolean {
  switch (request.op) {
    case "rename":
      return request.newName !== "";
    case "save":
      return request.body !== "";
    case "move":
      return request.to !== "";
    default:
      return true;
  }
}

export function EditPanel({ state, layers, lockReason, touring, onChange, onPreview, onExecute, onClose }: EditPanelProps): JSX.Element {
  const { request, answer, error, busy } = state;
  const moveValue = request.to === "common" ? "common" : `${PROJECT_PREFIX}${request.toLayer}`;
  return (
    <section className="edit-panel" data-role="edit-panel" data-op={request.op} data-name={request.name}>
      {request.op === "save" && (
        <textarea
          id="edit-body"
          data-role="edit-body"
          rows={20}
          className="mono"
          value={request.body}
          onChange={(event) => onChange({ ...request, body: event.currentTarget.value })}
        />
      )}
      {request.op === "rename" && (
        <label>
          新しい名前{" "}
          <input
            type="text"
            id="edit-new-name"
            data-role="edit-new-name"
            className="mono"
            value={request.newName}
            onChange={(event) => onChange({ ...request, newName: event.currentTarget.value })}
          />
        </label>
      )}
      {request.op === "move" && (
        <select
          id="edit-to"
          data-role="edit-to"
          value={moveValue}
          onChange={(event) => {
            const value = event.currentTarget.value;
            onChange({
              ...request,
              to: value === "common" ? "common" : "project",
              toLayer: value === "common" ? "" : value.slice(PROJECT_PREFIX.length),
            });
          }}
        >
          <option value="common">共通（.ccnavi/config/common/templates）</option>
          {layers.map((layer) => (
            <option key={layer} value={`${PROJECT_PREFIX}${layer}`}>
              {layerLabel(layer)}の層
            </option>
          ))}
        </select>
      )}
      {(request.op === "delete" || request.op === "delete-draft") && (
        <p data-role="edit-confirm">
          {request.name} を削除します。削除した中身は戻せません（git に入れていなければ復元できません）。
        </p>
      )}
      {lockReason !== "" && (
        <div className="lock" role="status" data-banner="edit-lock">
          この操作は止めています: {lockReason}
        </div>
      )}
      {error !== "" && <p className="status failed">{error}</p>}
      {answer !== null && (
        <>
          <p>
            対象: <span className="mono">{answer.source}</span>
            {answer.target !== "" && answer.target !== answer.source ? (
              <>
                {" → "}
                <span className="mono">{answer.target}</span>
              </>
            ) : null}
          </p>
          <p className="hint">
            ハッシュ: <span className="mono" data-role="edit-sha">{answer.sha}</span>
          </p>
          {answer.errors.length > 0 && (
            <ul className="tpl-problems">
              {answer.errors.map((line, index) => (
                <li key={index} className="sev-error">
                  <span className="sev-label">[error]</span>
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          )}
          <Problems problems={answer.problems} />
          {answer.infos.length > 0 && (
            <ul className="tpl-problems">
              {answer.infos.map((line, index) => (
                <li key={index} className="sev-info">
                  <span className="sev-label">[info]</span>
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          )}
          {answer.diff.length > 0 && (
            <>
              <h3>差分（前後つき）</h3>
              <pre className="diff" data-role="edit-diff">
                {answer.diff.map((line, index) => (
                  <div key={index} className={diffClass(line)}>
                    {line}
                  </div>
                ))}
              </pre>
            </>
          )}
        </>
      )}
      <div className="edit-actions">
        <button type="button" className="action small" data-action="preview-edit" disabled={busy || touring || !filled(request)} onClick={onPreview}>
          プレビュー
        </button>
        {answer !== null && (
          <button
            type="button"
            className="action primary"
            data-action="edit-yes"
            disabled={!answer.ok || !/^[0-9a-f]{64}$/.test(answer.sha) || lockReason !== "" || busy || touring}
            onClick={() => onExecute(answer.sha)}
          >
            この内容で{VERB[request.op]}
          </button>
        )}
        <button type="button" className="action small" data-action="close-edit" onClick={onClose}>
          閉じる
        </button>
      </div>
    </section>
  );
}
