/**
 * 直前の操作の一言（role="status"）。何も言っていない間も、読み上げの場所として置いておく。
 * 同じ一言が続いても、言い直すたびに中身を作り直す（見た目が変わり、読み上げもやり直される）。
 */
import { useRef, type JSX } from "react";

import type { Said } from "./api.js";

export function Status({ id, said }: { readonly id: string; readonly said: Said | undefined }): JSX.Element {
  const last = useRef(said);
  const seq = useRef(0);
  if (last.current !== said) {
    last.current = said;
    seq.current += 1;
  }
  const className = said === undefined ? "status" : said.ok ? "status ok" : "status error";
  return (
    <p id={id} role="status" className={className}>
      <span key={seq.current}>{said?.text ?? ""}</span>
    </p>
  );
}
