/**
 * 「このPCでは未準備」のカード。親のワークツリーが無いPCで出す。
 *
 * 親のチケットは親のワークツリー（`.claude/worktrees/<親>`）の中にだけあり、ほかのツリーのコピーでは
 * 代用しない。だからこのPCにはチケットが出ない。代わりに、ブランチ・MR の番号（取り込み状態の控え）・
 * 止まった理由を出し、「準備する」で `ccnavi-start.sh --mr <番号>`（親のワークツリーを切る入口）を呼ぶ。
 * MR の番号が控えに無いときはボタンを出さず、手で打つ入口を案内する。
 */
import type { JSX } from "react";

import type { Unprepared } from "../../core/board.js";
import { post } from "./post.js";

export function UnpreparedList({ items }: { readonly items: readonly Unprepared[] }): JSX.Element | null {
  if (items.length === 0) {
    return null;
  }
  return (
    <ul className="unprepared" data-tour="unprepared">
      {items.map((item) => (
        <UnpreparedCard key={`${item.project}/${item.family}`} item={item} />
      ))}
    </ul>
  );
}

function UnpreparedCard({ item }: { readonly item: Unprepared }): JSX.Element {
  return (
    <li className="unprepared-card" data-family={item.family}>
      <div className="unprepared-head">
        <strong className="unprepared-title">このPCでは未準備</strong>
        <span className="unprepared-family">{item.family}</span>
        {item.project !== "" ? <span className="unprepared-project">{item.project}</span> : null}
      </div>
      <div className="unprepared-facts">
        {item.branch !== "" ? <span className="unprepared-branch">ブランチ {item.branch}</span> : null}
        {item.mr !== null ? <span className="unprepared-mr">MR !{item.mr}</span> : null}
      </div>
      {item.reason !== "" ? <p className="unprepared-reason">{item.reason}</p> : null}
      {item.mr !== null ? (
        <button
          type="button"
          className="unprepared-prepare"
          data-action="prepare"
          title="ccnavi-start.sh --mr で、このPCに親のワークツリーを切ります"
          onClick={() => post({ type: "prepare", family: item.family, project: item.project })}
        >
          準備する
        </button>
      ) : (
        <p className="unprepared-hint">MR の番号が控えにありません。ccnavi-start.sh --mr &lt;番号&gt; を端末で打ってください。</p>
      )}
    </li>
  );
}
