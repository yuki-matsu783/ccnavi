/**
 * 直前の操作の一言（保存中・保存できない理由・検証のerrorなど）。ツールバー（`ScreenHeader`）の中の最後の行に出す。
 * ツールバーは画面の上に貼り付くので、押したボタンのすぐ下に結果が出て、一覧をスクロールしても見えたままになる。
 * 一言が無いときは行ごと消す（ツールバーの高さを無駄に取らない）。
 */
import type { JSX } from "react";
import { Code } from "./Code.js";

export interface StatusText {
  readonly text: string;
  readonly error: boolean;
}

export function StatusLine({ status }: { readonly status: StatusText }): JSX.Element {
  const classes = ["status-line"];
  if (status.error) {
    classes.push("error");
  }
  if (status.text === "") {
    classes.push("hidden");
  }
  return (
    <p id="status" className={classes.join(" ")} role="status" aria-live="polite" data-tour="status">
      <Code text={status.text} />
    </p>
  );
}
