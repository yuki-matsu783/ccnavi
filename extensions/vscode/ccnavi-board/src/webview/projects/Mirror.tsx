/**
 * 共通レイヤーのミラーの表示。カードの「ミラー」の欄（状態・ずれの中身・「ミラーを同期」）、上部の「すべて同期」と最終確認の時刻、
 * 確認できないときの帯。
 *
 * 見せる中身は拡張ホストが渡す（`ProjectsData` の `mirror`）。ボタンを押せるかと文言は `core/mirrormodel.ts` が決め、
 * ここは並べるだけ。同期するのは拡張ホスト（実行ファイル）で、画面は押されたことを送るだけ。
 */
import type { JSX } from "react";

import { actionText, mirrorOf, projectSyncButton, stateText, syncAllButton, type MirrorProject } from "../../core/mirrormodel.js";
import type { MirrorData, ProjectsMessage } from "../../core/projects-view.js";

type Act = (message: ProjectsMessage) => void;

/** カードの「ミラー」の欄 */
export function MirrorField({ mirror, name, act }: { readonly mirror: MirrorData; readonly name: string; readonly act: Act }): JSX.Element {
  const button = projectSyncButton(mirror.status, mirror.busy, name);
  const project = mirror.status.kind === "ok" && mirror.status.list.ok ? mirrorOf(mirror.status.list, name) : undefined;
  return (
    <div className="mirror-field">
      <div className="mirror-line">
        <MirrorState mirror={mirror} project={project} />
        <button
          type="button"
          className="action small"
          data-action="mirror-sync"
          disabled={!button.enabled}
          title={button.enabled ? "共通レイヤーの内容を、このプロジェクトのワークツリーにコピーします。コミットはしません" : button.reason}
          onClick={() => {
            if (project !== undefined) {
              act({ type: "mirrorSync", names: [project.name], expect: project.expect });
            }
          }}
        >
          ミラーを同期
        </button>
      </div>
      {project !== undefined && project.state === "differs" && project.changes.length > 0 && <Changes project={project} />}
    </div>
  );
}

/** 状態の一言。一覧全体を確認できないときの理由は帯（`MirrorBanners`）に出すので、ここでは繰り返さない */
function MirrorState({ mirror, project }: { readonly mirror: MirrorData; readonly project: MirrorProject | undefined }): JSX.Element {
  const status = mirror.status;
  if (status.kind === "waiting") {
    return <span className="mirror-state dim">確認しています…</span>;
  }
  if (status.kind !== "ok" || !status.list.ok) {
    return <span className="mirror-state dim">確認できません</span>;
  }
  if (project === undefined) {
    return <span className="mirror-state dim">確認結果がありません</span>;
  }
  const className = project.state === "synced" ? "ok" : "warn-text";
  return (
    <span className="mirror-state">
      <span className={className}>{stateText(project)}</span>
      {project.state === "error" && project.error !== "" && <span className="small dim"> {project.error}</span>}
    </span>
  );
}

/** ずれの中身。既定は畳んでおき、開くと 1 ファイルずつ種類とパスが出る */
function Changes({ project }: { readonly project: MirrorProject }): JSX.Element {
  return (
    <details className="mirror-changes">
      <summary>変わるファイルを見る</summary>
      <ul>
        {project.changes.map((c) => (
          <li key={`${c.action}:${c.rel}`} data-action-kind={c.action}>
            <span className={`badge mirror-${c.action}`}>{actionText(c.action)}</span> <span className="mono small">{c.rel}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** 上部の「すべて同期（N 件）」 */
export function SyncAllButton({ mirror, act }: { readonly mirror: MirrorData; readonly act: Act }): JSX.Element {
  const button = syncAllButton(mirror.status, mirror.busy);
  const expect = mirror.status.kind === "ok" ? mirror.status.list.expect : "";
  return (
    <button
      type="button"
      className={`action${mirror.busy ? " busy" : ""}`}
      data-action="mirror-sync-all"
      disabled={!button.enabled}
      title={button.enabled ? "ずれのある全プロジェクトに、共通レイヤーの内容をコピーします。書くのは各プロジェクトのワークツリーで、コミットはしません" : button.reason}
      onClick={() => act({ type: "mirrorSync", names: [], expect })}
    >
      <span className="spin" aria-hidden="true" />
      すべて同期（{button.count} 件）
    </button>
  );
}

/** ミラーの最終確認の時刻。まだ確認していなければ出さない */
export function CheckedAt({ mirror }: { readonly mirror: MirrorData }): JSX.Element | null {
  if (mirror.checkedAt === "") {
    return null;
  }
  return <span className="mirror-checked dim">ミラーの最終確認 {mirror.checkedAt}</span>;
}

/** ミラーを確認できないとき・コピーするものが無いときの帯 */
export function MirrorBanners({ mirror }: { readonly mirror: MirrorData }): JSX.Element | null {
  const status = mirror.status;
  if (status.kind === "unsupported") {
    return <div className="banner warn mirror-banner">ミラーのずれを確認できません。{status.reason}</div>;
  }
  if (status.kind === "failed") {
    return <div className="banner warn mirror-banner">ミラーのずれを確認できませんでした。{status.error}</div>;
  }
  if (status.kind === "ok" && !status.list.ok) {
    return <div className="banner warn mirror-banner">ミラーのずれを確認できませんでした。{status.list.error}</div>;
  }
  if (status.kind === "ok" && status.list.commonEmpty) {
    return (
      <div className="banner mirror-banner">
        共通レイヤー（<code>.ccnavi/config/common/</code>）にも工程スキル（<code>skills/</code>）にもコピーするものがありません。同期しても何も変わらないので、同期のボタンは押せません。
      </div>
    );
  }
  return null;
}
