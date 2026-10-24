/**
 * マージ状況の帯。ツールバーの「マージ状況を確認」の結果を、ボードの上に 1 親 1 行で出す。
 *
 * 見せる中身は拡張ホストが持つ（`MergeView`。ボードが開いている間だけ）。ここは渡されたものを出すだけで、判定しない。
 * 行は次のとおり（題は、ボードのアーカイブにある同じ親から取る。無ければ識別子だけ）。
 *
 * - マージ待ち: `<親> <題> MR !N - マージ待ち（Draft なら付記）` ［MR を開く］
 * - マージ済み: ［片付ける］（確認のダイアログのあと、sync のコマンドを送る。送ったら、次の確認まで押せない）
 * - マージせず閉じた: 自動では片付けない。捨てた親なら、ユーザが端末で記録を消すコマンド（コピーのボタンだけ。ボードからは送らない）
 * - 確認できない（種類と理由）／記録が不完全
 *
 * ボタンとリンクは `card-button`（操作の MR リンクも同じ寸法）。リンクは http(s) のときだけ。
 */
import type { JSX } from "react";

import { archivedTitleOf, type Board } from "../../core/board.js";
import type { MergeView, MergeViewRow } from "../../core/merge.js";
import { post } from "./post.js";
import { isHttpUrl } from "./text.js";

/** 確認できなかった理由の種類の呼び名。画面の言葉（sh の種類の文字列はそのまま出さない） */
const REASON_LABELS: Readonly<Record<string, string>> = {
  "no-tool": "道具が無い",
  auth: "認証",
  network: "ネットワーク",
  api: "ホストの API",
  "not-found": "見つからない",
  timeout: "期限切れ",
  format: "出力の形が想定外",
  failed: "実行できない",
  "no-number": "MR の番号が無い",
};

export function MergeBand({ merge, board, touring }: { readonly merge: MergeView; readonly board: Board; readonly touring: boolean }): JSX.Element {
  return (
    <section className="merge-band" data-tour="merge-band" aria-label="マージ状況" aria-busy={merge.checking ? "true" : undefined}>
      <h2 className="merge-head">マージ状況</h2>
      {merge.error !== "" ? (
        <p className="merge-error" role="alert" data-merge="list-error">
          {merge.error}
        </p>
      ) : merge.rows.length === 0 ? (
        merge.checking ? (
          <p className="merge-note">確認中…</p>
        ) : (
          <p className="merge-note" data-merge="empty">
            マージ待ちの親はありません
          </p>
        )
      ) : (
        <ul className="merge-rows">
          {merge.rows.map((row) => (
            <Row key={row.parent} row={row} title={archivedTitleOf(board, row.project, row.parent)} touring={touring} />
          ))}
        </ul>
      )}
      {merge.checkedAt !== "" ? (
        <p className="merge-time" data-merge="checked-at">
          確認した時刻 {merge.checkedAt}
        </p>
      ) : null}
    </section>
  );
}

function Row({ row, title, touring }: { readonly row: MergeViewRow; readonly title: string | undefined; readonly touring: boolean }): JSX.Element {
  const mr = row.mr === null ? "" : `MR !${row.mr}`;
  return (
    <li className={`merge-row merge-${row.state}`} data-parent={row.parent} data-state={row.state}>
      <span className="merge-who">
        <span className="num">{row.parent}</span>
        {title !== undefined ? <> <span className="title">{title}</span></> : null}
        {mr !== "" ? <> <span className="merge-mr">{mr}</span></> : null}
      </span>
      <span className="merge-state">
        <State row={row} touring={touring} />
      </span>
    </li>
  );
}

function State({ row, touring }: { readonly row: MergeViewRow; readonly touring: boolean }): JSX.Element {
  switch (row.state) {
    case "checking":
      return <span>確認中…</span>;
    case "open":
      return (
        <>
          <span>- マージ待ち{row.draft ? "（Draft）" : ""}</span> <MrLink url={row.url} />
        </>
      );
    case "merged":
      return (
        <>
          <span>- マージ済み</span>{" "}
          {row.sent ? (
            <span className="merge-sent" role="status">
              片付けのコマンドを送信しました。結果はターミナルで確認してください
            </span>
          ) : (
            <button
              type="button"
              className="card-button primary"
              data-action="merge-retire"
              data-tour="merge-retire"
              data-parent={row.parent}
              disabled={row.busy}
              title="ccnavi-sync.sh をターミナルで実行します（確認のダイアログが出ます）"
              onClick={() => {
                if (!touring) {
                  post({ type: "mergeRetire", parent: row.parent });
                }
              }}
            >
              片付ける
            </button>
          )}
          {row.sendError !== "" ? (
            <span className="merge-error" role="alert" data-merge="send-error">
              送信できませんでした: {row.sendError}
            </span>
          ) : null}
        </>
      );
    case "closed":
      return (
        <>
          <span>
            - マージせず閉じた - 自動では片付けません。捨てた親なら、ユーザが端末で{" "}
            <code className="merge-command">{row.abandon}</code> を実行して記録を消します
          </span>
          <MrLink url={row.url} />{" "}
          <button
            type="button"
            className="card-button"
            data-action="merge-copy-abandon"
            data-tour="merge-abandon"
            data-parent={row.parent}
            onClick={() => {
              if (!touring) {
                post({ type: "mergeCopyAbandon", parent: row.parent });
              }
            }}
          >
            コマンドをコピー
          </button>
        </>
      );
    case "unknown":
      return (
        <span className="merge-unknown" data-reason={row.reason}>
          - 確認できない（{REASON_LABELS[row.reason] ?? row.reason}: {row.detail}）
        </span>
      );
    case "incomplete":
      return <span className="merge-unknown">- 確認できない（記録が不完全: {row.detail}）</span>;
  }
}

/** 操作の MR リンク。URL が http(s) のときだけ（それ以外は状態だけを出し、リンクにしない） */
function MrLink({ url }: { readonly url: string }): JSX.Element | null {
  return isHttpUrl(url) ? (
    <a className="card-button" data-action="open-mr" href={url} title="マージリクエストを開きます">
      MR を開く
    </a>
  ) : null;
}
