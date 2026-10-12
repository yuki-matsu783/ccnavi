/**
 * 取り込みの下見の答え。差分（同名がある）か取り込む本文（新しく置く）を出し、問題と錠を見せてから、取り込むボタンを出す。
 *
 * 実行ファイルが返した文字列は文字としてそのまま出す（HTML として解釈しない。`\uXXXX` への置き換えは実行ファイルがしている）。
 * 取り込めるかは答えの `ok` と `draft` が決める。画面は読んで判断するだけにする。
 */
import type { JSX } from "react";

import type { Lock } from "../../core/lock.js";
import type { ImportAnswer, ImportRequest } from "../../core/templates-view.js";
import { Problems } from "./Problems.js";

/** 下見の状態。`request` は下見を頼んだときの要求、`answer` は答え（読めなければ null）、`error` は読めなかった理由 */
export interface PreviewState {
  readonly request: ImportRequest;
  readonly answer: ImportAnswer | null;
  readonly error: string;
}

export interface PreviewProps {
  readonly request: ImportRequest;
  readonly answer: ImportAnswer | null;
  readonly error: string;
  /** 取り込み先の錠（共通なら全体、プロジェクトならそのプロジェクトの錠） */
  readonly lock: Lock;
  /** 取り込みの往復の途中か、案内の間か */
  readonly busy: boolean;
  readonly onImport: (expect: string) => void;
  readonly onClose: () => void;
}

/** 差分の 1 行の色の区別。先頭の記号は消さず、色だけに頼らないようにする */
function diffClass(line: string): string {
  if (line.startsWith("@@")) {
    return "diff-hunk";
  }
  if (line.startsWith("+")) {
    return "diff-add";
  }
  if (line.startsWith("-")) {
    return "diff-del";
  }
  return "diff-ctx";
}

export function Preview({ answer, error, lock, busy, onImport, onClose }: PreviewProps): JSX.Element {
  if (error !== "" || answer === null) {
    return (
      <section className="import-preview" data-role="import-preview">
        <p className="status failed">{error}</p>
        <button type="button" className="action small" data-action="close-preview" onClick={onClose}>
          閉じる
        </button>
      </section>
    );
  }
  const draft = answer.draft;
  return (
    <section className="import-preview" data-role="import-preview">
      <p>
        取り込み先: <span className="mono">{answer.target}</span>（{answer.exists ? "置き換える" : "新しく置く"}）
      </p>
      {draft !== null && (
        <p className="hint">
          下書きのハッシュ: <span className="mono" data-role="draft-sha">{draft.sha}</span>
        </p>
      )}
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
      {answer.exists && (
        <>
          <h3>差分（前後つき）</h3>
          <pre className="diff" data-role="diff">
            {answer.diff.map((line, index) => (
              <div key={index} className={diffClass(line)}>
                {line}
              </div>
            ))}
          </pre>
        </>
      )}
      {!answer.exists && (
        <>
          <h3>取り込む本文（フローの文は案内文になるので、読んでから取り込む）</h3>
          <pre className="new-text" data-role="new-text">
            {answer.new_text}
          </pre>
        </>
      )}
      {lock.locked && (
        <div className="lock" role="status" data-banner="import-lock">
          取り込みは止めています: {lock.reason}
        </div>
      )}
      <div className="import-actions">
        <button
          type="button"
          className="action primary"
          data-action="import-yes"
          disabled={!answer.ok || draft === null || lock.locked || busy}
          onClick={() => {
            if (draft !== null) {
              onImport(draft.sha);
            }
          }}
        >
          この内容で取り込む
        </button>
        <button type="button" className="action small" data-action="close-preview" onClick={onClose}>
          閉じる
        </button>
      </div>
    </section>
  );
}
