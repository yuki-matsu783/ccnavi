/**
 * 直前の操作の一言（role="status"）。何も言っていない間も、読み上げの場所として置いておく。
 */
import type { JSX } from "react";

import type { Said } from "./api.js";

export function Status({ id, said }: { readonly id: string; readonly said: Said | undefined }): JSX.Element {
  const className = said === undefined ? "status" : said.ok ? "status ok" : "status error";
  return (
    <p id={id} role="status" className={className}>
      {said?.text ?? ""}
    </p>
  );
}
