/**
 * 担当に渡る手順のプレビュー。`SubagentStart` で担当のサブエージェントに渡る手順の行を、実行ファイル
 * （`--lint --json --flow` の `flow.rendered`）が並べたとおりに見せる。画面は並べ直さない。
 *
 * 編集が止まるたびに、呼び手（`App.tsx`）が実行ファイルに確かめ直させる。確かめている間は、最後に確認が済んだ答えを
 * 残し、そう言う。開閉できる区画（`<details>`）で、既定は開いておく。
 */
import type { JSX } from "react";

import type { FlowChecks } from "../../core/flow-view.js";

export interface PreviewProps {
  readonly checks: FlowChecks | undefined;
  /** 確かめ直している最中（出しているのは前の答え） */
  readonly checking: boolean;
  /** 確かめられなかった理由（実行ファイルを走らせられない・error がある） */
  readonly error: string | undefined;
}

function body(checks: FlowChecks | undefined): JSX.Element {
  if (checks === undefined) {
    return <p className="dim small">まだ実行ファイルで確認していません。</p>;
  }
  if (checks.rendered === null) {
    return <p className="dim small">実行ファイルがこのフローを手順に並べられませんでした（中身を読み込めません）。担当にはフローの手順が渡りません。</p>;
  }
  return <pre className="flow-rendered">{checks.rendered.join("\n")}</pre>;
}

export function Preview({ checks, checking, error }: PreviewProps): JSX.Element {
  return (
    <details className="flow-preview" id="flow-preview" data-tour="preview" open>
      <summary title="SubagentStart で担当のサブエージェントに渡る手順です。実行ファイル（ccnavi --lint --flow）が並べたものです">担当に渡る手順</summary>
      <p className="dim small" id="flow-preview-lead">
        子チケットを担当するサブエージェントに渡る手順です。編集するとここに反映されます
      </p>
      {/* 結果がまだ無い（確認していない）ときは、前の答えが無いので確認中の文は出さない。今の状態は body の「まだ確認していません」が示す */}
      {checking && checks !== undefined && (
        <p className="dim small" id="flow-preview-checking">
          確認中です。下の手順は、最後に確認が済んだときのものです
        </p>
      )}
      {error !== undefined && <p className="error small" id="flow-preview-error">{error}</p>}
      {body(checks)}
    </details>
  );
}
