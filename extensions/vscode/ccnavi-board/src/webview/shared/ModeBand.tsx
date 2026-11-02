/**
 * 動作モード（`CCNAVI_MODE`）の帯。実行ファイルが返したモードを言い換えるだけで、enableと空は何も出さない。
 * ルール管理とチケット管理で共有する。
 */
import type { JSX } from "react";
import { modeBanner } from "../../core/mode-banner.js";

export function ModeBand({ mode }: { readonly mode: string }): JSX.Element | null {
  const text = modeBanner(mode);
  if (text === "") {
    return null;
  }
  return (
    <div className="banner warn mode-band">
      現在の<code>CCNAVI_MODE</code>: <strong>{mode}</strong>。{text}
    </div>
  );
}
