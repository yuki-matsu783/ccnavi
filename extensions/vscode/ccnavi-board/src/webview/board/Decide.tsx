/**
 * 残った指摘の対応方針を決めるオーバーレイの中身。［レビュー完了］を押すとここに来る。
 *
 * - 未解決が 0 件（正しく読めたときだけ。読めなかったときはここに来ない）: 「フェーズ N に未解決の指摘はありません」と
 *   主ボタン「レビュー済みにする」
 * - 1 件以上: 指摘を 1 件ずつ並べ、対応方針（対応しない・このフェーズで直す・issue に回す）を選ばせる。何も選ばなければ
 *   全部「このフェーズで直す」。2 件以上なら、全部を一括で同じ対応方針にする小さな行を出す。副ボタン「エージェントに
 *   相談するプロンプトをコピー」は、オーバーレイを閉じず、選びかけの対応方針も変えない
 *
 * 選び直した対応方針だけをここに持つ。見せる指摘・ダイジェスト・置いている最中かは拡張ホストが持ち、
 * ここは渡されたものを見せるだけ。押したら選んだ対応方針をキーごとに返す（`decideConfirm`）。
 */
import { useState, type JSX } from "react";

import { DECIDE_CHOICES, DECIDE_LABELS, type DecideChoice, type DecidePreview } from "../../core/decidemodel.js";
import { DialogActions } from "../shared/Dialog.js";
import { post } from "./post.js";
import { isHttpUrl } from "./text.js";

const DEFAULT_CHOICE: DecideChoice = "fix";

export function DecideBody({
  preview,
  deciding,
  notice,
  consult,
}: {
  readonly preview: DecidePreview;
  readonly deciding: boolean;
  readonly notice: string | undefined;
  readonly consult: { readonly ok: boolean; readonly text: string } | undefined;
}): JSX.Element {
  // 見せ直したら（ダイジェストが変わったら）選び直す。前の一覧で選んだ対応方針を、別の指摘に持ち越さない。
  // 相談のプロンプトをコピーしても、見せ直しではない（ダイジェストが同じ）ので選びかけは保たれる
  const [picked, setPicked] = useState<{ readonly digest: string; readonly choices: Record<string, DecideChoice> }>({
    digest: preview.digest,
    choices: {},
  });
  // 何も選ばなければ、全部「このフェーズで直す」。選び直したものだけを上書きする
  const overrides = picked.digest === preview.digest ? picked.choices : {};
  const choices: Record<string, DecideChoice> = Object.fromEntries(
    preview.threads.map((t) => [t.key, overrides[t.key] ?? DEFAULT_CHOICE]),
  );
  const choose = (key: string, choice: DecideChoice): void =>
    setPicked({ digest: preview.digest, choices: { ...choices, [key]: choice } });
  const chooseAll = (choice: DecideChoice): void =>
    setPicked({ digest: preview.digest, choices: Object.fromEntries(preview.threads.map((t) => [t.key, choice])) });
  const count = preview.threads.length;
  return (
    <>
      <h2 id="approval-title">
        {count === 0
          ? `フェーズ ${preview.phase} に未解決の指摘はありません`
          : `フェーズ ${preview.phase} で未解決（Unresolved）の指摘 ${count} 件の対応方針`}
      </h2>
      {notice ? <p className="approval-note warn">{notice}</p> : null}
      <p className="approval-note">
        親 {preview.parent} のマージリクエスト{preview.mr.url ? ` ${preview.mr.url}` : ""}。
        {/* 選ぶものが無いときは、選び方の注意を出さない。0 件なら押すとレビュー済みになるだけ */}
        {count === 0
          ? "このフェーズをレビュー済みにしますか？"
          : "「このフェーズで直す」を 1 件でも選ぶと、追加対応用の子チケットを作成し、このフェーズを再開します。"}
      </p>
      {/* 2 件以上のときだけ、全部を同じ対応方針にする小さな行を出す。個別の選択は上書きされる */}
      {count < 2 ? null : (
        <div className="decide-bulk">
          <span>すべて:</span>
          {DECIDE_CHOICES.map((c) => (
            <button
              key={c}
              type="button"
              className="decide-bulk-button"
              data-bulk={c}
              disabled={deciding}
              onClick={() => chooseAll(c)}
            >
              {DECIDE_LABELS[c]}
            </button>
          ))}
        </div>
      )}
      {count === 0 ? null : (
        <ol className="decide-threads" data-tour="decide-threads">
          {preview.threads.map((t) => (
            <li key={t.key} className="decide-thread" data-key={t.key}>
              <div className="decide-where">
                <span className="approval-id">{t.path ? `${t.path}:${t.line}` : "（場所なし）"}</span>
                {/* 指摘の URL。http(s) のときだけリンクにする（そうでなければ文字として出すだけ） */}
                {t.url ? (
                  isHttpUrl(t.url) ? (
                    <a className="decide-url" href={t.url}>
                      {t.url}
                    </a>
                  ) : (
                    <span className="decide-url">{t.url}</span>
                  )
                ) : null}
              </div>
              <Body body={t.body} />
              <div className="decide-choices" role="radiogroup" aria-label={`${t.path || t.key} の対応方針`}>
                {DECIDE_CHOICES.map((c) => (
                  <label key={c} className="decide-choice">
                    <input
                      type="radio"
                      name={`decide-${t.key}`}
                      value={c}
                      data-choice={c}
                      checked={choices[t.key] === c}
                      disabled={deciding}
                      onChange={() => choose(t.key, c)}
                    />
                    <span>{DECIDE_LABELS[c]}</span>
                  </label>
                ))}
              </div>
            </li>
          ))}
        </ol>
      )}
      {consult ? (
        <p className={consult.ok ? "approval-note" : "approval-note error"} role="status" data-consult={consult.ok ? "ok" : "error"}>
          {consult.text}
        </p>
      ) : null}
      <DialogActions className="approval-actions">
        <button
          type="button"
          className="action primary"
          data-action="decide-confirm"
          data-tour="decide-confirm"
          disabled={deciding}
          onClick={() => post({ type: "decideConfirm", choices })}
        >
          {deciding ? "反映中…" : count === 0 ? "レビュー済みにする" : "この対応方針を確定"}
        </button>
        {count === 0 ? null : (
          <button
            type="button"
            className="action"
            data-action="decide-consult"
            data-tour="decide-consult"
            disabled={deciding}
            title="未解決の指摘を要約し、対応方針の案を返してもらうプロンプトをコピーします。読み取りだけを頼むので、対応方針を確定する操作は含みません。対応方針は、この画面で決めます"
            onClick={() => post({ type: "decideConsult" })}
          >
            エージェントに相談するプロンプトをコピー
          </button>
        )}
        <button
          type="button"
          className="action"
          data-action="approve-cancel"
          disabled={deciding}
          onClick={() => post({ type: "approveCancel" })}
        >
          キャンセル
        </button>
      </DialogActions>
    </>
  );
}

/** 指摘の本文。1 行目を出し、2 行以上あれば `<details>` で全文を開ける */
function Body({ body }: { readonly body: string }): JSX.Element {
  const lines = body.split("\n");
  const first = (lines.find((l) => l.trim() !== "") ?? "").trim();
  const multiple = lines.filter((l) => l.trim() !== "").length > 1;
  return (
    <div className="decide-body" data-body>
      <div className="decide-first">{first}</div>
      {multiple ? (
        <details className="decide-more">
          <summary>全文</summary>
          <pre className="decide-full">{body}</pre>
        </details>
      ) : null}
    </div>
  );
}
