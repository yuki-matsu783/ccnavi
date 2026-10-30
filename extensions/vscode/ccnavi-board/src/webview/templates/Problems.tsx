/**
 * 苦情の一覧。深刻度は色と文字の両方で出す（色だけに頼らない）。detail は文字としてそのまま出す（HTML として解釈しない）。
 */
import type { JSX } from "react";

import { Code } from "../shared/Code.js";
import type { TemplateProblem } from "../../core/templates-view.js";

export function Problems({ problems }: { readonly problems: readonly TemplateProblem[] }): JSX.Element | null {
  if (problems.length === 0) {
    return null;
  }
  return (
    <ul className="tpl-problems">
      {problems.map((p, index) => (
        <li key={index} className={`sev-${p.severity}`}>
          <span className="sev-label">[{p.severity}]</span> <span>
            <Code text={p.detail} />
          </span>
        </li>
      ))}
    </ul>
  );
}
