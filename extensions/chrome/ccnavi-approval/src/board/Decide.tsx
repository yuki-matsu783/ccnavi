/**
 * 残った指摘の行き先を決める画面（`div.decide`）。レビューの欄（`Review.tsx`）の中に出る。
 *
 * - 指摘を 1 件ずつ並べ、行き先（対応しない・このフェーズで直す・issue に回す）を選ばせる。何も選ばなければ全件「このフェーズで直す」。
 *   2 件以上なら、全件を同じ行き先にする小さな行を出す（個別の選び直しはそのあとも効く）
 * - 解決できない・Chrome で扱えない指摘（`unsupported`）は選択肢を出さず理由を出す。1 件でもあれば確定のボタンは出さない
 * - 0 件なら押すとレビュー済みになるだけ
 *
 * 選び直した行き先だけをここに持つ（見せ直して `digest` が変わったら捨てる）。見せる指摘・確定の最中かは controller が持ち、
 * ここは渡されたものを描く。本文は承認の画面と同じ消毒（`Markdown.tsx`）で描く。
 */
import { useContext, useState, type JSX } from "react";

import { DECIDE_CHOICES, DECIDE_LABELS, type DecideChoice, type DecidePreview } from "../core/reviewed.js";
import { ALLOWED_URI } from "../core/sanitize.js";
import { Markdown } from "./Markdown.js";
import { BusyContext } from "./parts.js";

const DEFAULT_CHOICE: DecideChoice = "fix";

export interface DecideProps {
  readonly preview: DecidePreview;
  readonly deciding: boolean;
  readonly notice: string;
  /** 見せ直したときに引き継いだ選択 */
  readonly inherited?: Readonly<Record<string, DecideChoice>>;
  readonly onConfirm: (choices: Record<string, DecideChoice>, digest: string) => void;
  readonly onCancel: () => void;
}

export function Decide({ preview, deciding, notice, inherited, onConfirm, onCancel }: DecideProps): JSX.Element {
  const busy = useContext(BusyContext) || deciding;
  // 見せ直したら（ダイジェストが変わったら）選び直す。前の一覧の選択を別の指摘に持ち越さない
  const [picked, setPicked] = useState<{ readonly digest: string; readonly choices: Readonly<Record<string, DecideChoice>> }>({ digest: preview.digest, choices: {} });
  const overrides = picked.digest === preview.digest ? picked.choices : (inherited ?? {});
  const supported = preview.threads.filter((t) => !t.unsupported);
  const choices: Record<string, DecideChoice> = Object.fromEntries(supported.map((t) => [t.key, overrides[t.key] ?? DEFAULT_CHOICE]));
  const choose = (key: string, choice: DecideChoice): void => setPicked({ digest: preview.digest, choices: { ...choices, [key]: choice } });
  const chooseAll = (choice: DecideChoice): void => setPicked({ digest: preview.digest, choices: Object.fromEntries(supported.map((t) => [t.key, choice])) });
  const count = preview.threads.length;
  const blocked = preview.threads.some((t) => t.unsupported);
  return (
    <div className="decide" data-phase={String(preview.phase)}>
      <h5>{count === 0 ? `フェーズ ${preview.phase} に未解決の指摘はありません` : `フェーズ ${preview.phase} で未解決の指摘 ${count} 件の行き先`}</h5>
      {notice ? <p className="notice warn" role="status">{notice}</p> : null}
      {count > 0 ? <p className="decide-note">「このフェーズで直す」を 1 件でも選ぶと、続きの子チケットを作り、このフェーズを再開します。</p> : null}
      {supported.length >= 2 && count >= 2 ? (
        <div className="decide-bulk">
          <span>すべて:</span>
          {DECIDE_CHOICES.map((c) => (
            <button key={c} type="button" className="decide-bulk-button" data-bulk={c} disabled={busy} onClick={() => chooseAll(c)}>
              {DECIDE_LABELS[c]}
            </button>
          ))}
        </div>
      ) : null}
      {count === 0 ? null : (
        <ol className="decide-threads">
          {preview.threads.map((t) => (
            <li key={t.key} className="decide-thread" data-key={t.key}>
              <div className="decide-where">
                <span className="decide-path">{t.path ? `${t.path}:${t.line}` : "（場所なし）"}</span>
                {t.url ? (
                  ALLOWED_URI.test(t.url.trim()) && /^https?:/i.test(t.url.trim()) ? (
                    <a className="decide-url" href={t.url} target="_blank" rel="noopener noreferrer">
                      {t.url}
                    </a>
                  ) : (
                    <span className="decide-url">{t.url}</span>
                  )
                ) : null}
              </div>
              <Body body={t.body} />
              {t.unsupported ? (
                <p className="notice error decide-unsupported" role="alert">{`Chrome では扱えません。VS Code で。${t.unsupported}`}</p>
              ) : (
                <div className="decide-choices" role="radiogroup" aria-label={`${t.path || t.key} の行き先`}>
                  {DECIDE_CHOICES.map((c) => (
                    <label key={c} className="decide-choice">
                      <input type="radio" name={`decide-${t.key}`} value={c} data-choice={c} checked={choices[t.key] === c} disabled={busy} onChange={() => choose(t.key, c)} />
                      <span>{DECIDE_LABELS[c]}</span>
                    </label>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
      <div className="actions">
        {blocked ? null : (
          <button type="button" className="action" data-action="decide-confirm" disabled={busy} onClick={() => onConfirm(choices, preview.digest)}>
            {deciding ? "反映中…" : count === 0 ? `フェーズ ${preview.phase} をレビュー済みにする` : "この行き先で確定"}
          </button>
        )}
        <button type="button" className="action" data-action="decide-cancel" disabled={busy} onClick={onCancel}>
          閉じる
        </button>
      </div>
    </div>
  );
}

/** 指摘の本文。1 行目を出し、2 行以上あれば `<details>` で全文を開ける（どちらも消毒した Markdown） */
function Body({ body }: { readonly body: string }): JSX.Element {
  const lines = body.split("\n");
  const first = (lines.find((l) => l.trim() !== "") ?? "").trim();
  const multiple = lines.filter((l) => l.trim() !== "").length > 1;
  return (
    <div className="decide-body" data-body>
      <Markdown text={first} />
      {multiple ? (
        <details className="decide-more">
          <summary>全文</summary>
          <Markdown text={body} />
        </details>
      ) : null}
    </div>
  );
}
