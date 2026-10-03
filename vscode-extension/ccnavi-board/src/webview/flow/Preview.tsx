/**
 * 担当に渡る手順のプレビュー。`SubagentStart` で担当のサブエージェントに渡る手順の行を、実行ファイル
 * （`--lint --json --flow` の `flow.rendered`）が並べたとおりに見せる。画面は並べ直さない。
 *
 * 編集するたびに、止まってから呼び手（`App.tsx`）が実行ファイルに確かめ直させる。確かめている間は前の答えを
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
    return <p className="dim small">まだ実行ファイルで確かめていない。</p>;
  }
  if (checks.rendered === undefined) {
    return <p className="dim small">実行ファイルが古いので、担当に渡る手順を表示できない（lint の答えに rendered が無い）。実行ファイルを更新してください。</p>;
  }
  if (checks.rendered === null) {
    return <p className="dim small">実行ファイルがこのフローを手順に並べられなかった（中身を読めない）。担当にはフローの手順が渡らない。</p>;
  }
  return <pre className="flow-rendered">{checks.rendered.join("\n")}</pre>;
}

export function Preview({ checks, checking, error }: PreviewProps): JSX.Element {
  return (
    <details className="flow-preview" id="flow-preview" open>
      <summary title="SubagentStart で担当のサブエージェントに渡る手順。実行ファイル（ccnavi --lint --flow）が並べたもの">担当に渡る手順</summary>
      {checking && (
        <p className="dim small" id="flow-preview-checking">
          確かめ直している…（表示しているのは前回の結果）
        </p>
      )}
      {error !== undefined && <p className="error small" id="flow-preview-error">{error}</p>}
      {body(checks)}
    </details>
  );
}
