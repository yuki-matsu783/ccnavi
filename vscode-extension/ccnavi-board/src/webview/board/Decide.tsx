/**
 * 残った指摘の行き先を決めるオーバーレイの中身。指摘を 1 件ずつ並べ、行き先（対応しない・
 * このフェーズで直す・issue に回す）を選ばせる。全部に選ぶまで「決める」は押せない。
 *
 * 選んでいる途中の行き先だけをここに持つ。見せる指摘・指紋・置いている最中かは拡張ホストが持ち、
 * ここは渡されたものを見せるだけ。押したら選んだ行き先を鍵ごとに返す（`decideConfirm`）。
 */
import { useState, type JSX } from "react";

import { DECIDE_CHOICES, DECIDE_LABELS, type DecideChoice, type DecidePreview } from "../../core/decidemodel.js";
import { post } from "./post.js";

export function DecideBody({
  preview,
  deciding,
  notice,
}: {
  readonly preview: DecidePreview;
  readonly deciding: boolean;
  readonly notice: string | undefined;
}): JSX.Element {
  // 見せ直したら（指紋が変わったら）選び直す。前の一覧で選んだ行き先を、別の指摘に持ち越さない
  const [picked, setPicked] = useState<{ readonly digest: string; readonly choices: Record<string, DecideChoice> }>({
    digest: preview.digest,
    choices: {},
  });
  const choices = picked.digest === preview.digest ? picked.choices : {};
  const choose = (key: string, choice: DecideChoice): void =>
    setPicked({ digest: preview.digest, choices: { ...choices, [key]: choice } });
  const offered = DECIDE_CHOICES.filter((c) => c !== "issue" || preview.can_issue);
  const count = preview.threads.length;
  const done = preview.threads.every((t) => t.key in choices);
  return (
    <>
      <h2 id="approval-title">
        {count === 0
          ? `フェーズ ${preview.phase} で未解決（Unresolved）の指摘なし`
          : `フェーズ ${preview.phase} で未解決（Unresolved）の指摘 ${count} 件の対応方針`}
      </h2>
      {notice ? <p className="approval-note warn">{notice}</p> : null}
      <p className="approval-note">
        親 {preview.parent} のマージリクエスト{preview.mr.url ? ` ${preview.mr.url}` : ""}。
        {/* 選ぶものが無いときは、選び方の注意を出さない。0 件なら押すとレビュー済みになるだけ */}
        {count === 0
          ? "このフェーズをレビュー済みにしますか？"
          : "「このフェーズで直す」を 1 件でも選ぶと、続きの子チケットを起こしてフェーズを開き直します。"}
        {count === 0 || preview.can_issue ? "" : " issue に回せるのは、フィードバック計画が承認されたあとです。"}
      </p>
      {count === 0 ? null : (
        <ol className="decide-threads">
          {preview.threads.map((t) => (
            <li key={t.key} className="decide-thread" data-key={t.key}>
              <div className="decide-where">
                <span className="approval-id">{t.path ? `${t.path}:${t.line}` : "（場所なし）"}</span>
                {t.url ? <span className="decide-url">{t.url}</span> : null}
              </div>
              <div className="decide-body" title={t.body}>
                {firstLine(t.body)}
              </div>
              <div className="decide-choices" role="radiogroup" aria-label={`${t.path || t.key} の対応方針`}>
                {offered.map((c) => (
                  <label key={c}>
                    <input
                      type="radio"
                      name={`decide-${t.key}`}
                      value={c}
                      data-choice={c}
                      checked={choices[t.key] === c}
                      disabled={deciding}
                      onChange={() => choose(t.key, c)}
                    />
                    {DECIDE_LABELS[c]}
                  </label>
                ))}
              </div>
            </li>
          ))}
        </ol>
      )}
      <div className="approval-actions">
        <button
          type="button"
          className="action primary"
          data-action="decide-confirm"
          disabled={deciding || !done}
          onClick={() => post({ type: "decideConfirm", choices })}
        >
          {deciding ? "反映中…" : count === 0 ? "レビュー済みにする" : "この方針で決める"}
        </button>
        <button
          type="button"
          className="action"
          data-action="approve-cancel"
          disabled={deciding}
          onClick={() => post({ type: "approveCancel" })}
        >
          やめる
        </button>
      </div>
    </>
  );
}

function firstLine(body: string): string {
  const line = body.split("\n").find((l) => l.trim() !== "") ?? "";
  return line.trim();
}
