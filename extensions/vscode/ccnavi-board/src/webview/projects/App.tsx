/**
 * プロジェクト管理画面の本体。帯・clone の欄・プロジェクトのカード・認識されない git・ワークスペース（プロジェクト外）。
 * ミラーの「すべて同期」と最終確認の時刻はヘッダに、カードごとの状態と「ミラーを同期」はカードに出す（`Mirror.tsx`）。
 *
 * 見せる中身は拡張ホストが渡す（`ProjectsData`）。画面が自分で持つのは、ユーザが触って決めるもの
 * （clone の欄、直前の操作の一言）だけ。clone もミラーの同期も書き込みも画面はしない。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { CloneStatus, ProjectsData, ProjectsPage, Stray, ToProjects } from "../../core/projects-view.js";
import { tourOf } from "../../core/tour-def.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import projectsTour from "../tours/projects.yml";
import { CheckedAt, MirrorBanners, SyncAllButton } from "./Mirror.js";
import { Project } from "./Project.js";
import { post } from "./post.js";
import { EMPTY, guessName, loadClone, saveClone, type CloneState } from "./state.js";
import { isTrackedProjectsDir } from "./text.js";

/** 案内（`tours/projects.yml`）。見本は使わず、画面は今の中身のまま指す */
const TOUR = tourOf(projectsTour);

export function App({ initial }: { readonly initial: ProjectsData }): JSX.Element {
  const [data, setData] = useState<ProjectsData>(initial);
  const [clone, setClone] = useState<CloneState>(loadClone);
  /**
   * 直前の操作の一言。生きている画面にしか届かないので、持ち越さない（拡張ホストも覚えない）。
   *
   * 消える条件は 2 つ。失敗（`failed`）は次の一覧が届いたら消す。 一覧が新しくなった後も
   * 「プロジェクト X が一覧に無い」が赤く残ると、ユーザはいまも失敗していると読む。
   * 案内（`info`）は残す。clone を送った直後は `.git` の出現で必ず読み直しが走るので、
   * ここで消すと案内が一瞬で消える
   */
  const [status, setStatus] = useState<CloneStatus | undefined>(undefined);

  const tour = useTour(data.kind === "page", { onEnd: () => post({ type: "tourDone" }) });
  const requestTour = tour.request;
  /** 拡張ホストへの送信。案内の間は実際の操作を送らない（`tourDone` と `ready` は直に送る） */
  const act = (message: Parameters<typeof post>[0]): void => {
    if (!tour.touring) {
      post(message);
    }
  };

  // 受け取る側（メッセージ）は描くたびに作り直さない。打ちかけの欄を消すのに今の値が要るので ref へ入れておく
  const cloneRef = useRef<CloneState>(clone);
  cloneRef.current = clone;

  const remember = (next: CloneState): void => {
    setClone(next);
    saveClone(next);
  };

  useHostMessages<ToProjects>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      setData(message.data);
      // 一覧が新しくなったので、古い一覧を見て出た失敗は、もう今の状態ではない
      setStatus((now) => (now?.kind === "failed" ? undefined : now));
    } else if (message.type === "tour") {
      requestTour();
    } else if (message.type === "failed") {
      setStatus({ kind: "failed", message: String(message.message ?? "") });
    } else if (message.type === "info") {
      setStatus({ kind: "info", message: String(message.message ?? "") });
    } else if (message.type === "cloned") {
      // clone をターミナルへ送った。同じものをもう一度送らないよう、打ち込んだ URL と名前を消す
      setClone(EMPTY);
      saveClone(EMPTY);
      setStatus({ kind: "info", message: String(message.message ?? "") });
    }
  }, () => post({ type: "ready" }));

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">プロジェクトの一覧を読み込めませんでした。原因を直してから「ccnavi ボード: プロジェクト管理を開く」を実行し直してください。</p>
        <pre className="load-error">{data.error}</pre>
      </>
    );
  }

  const page = data.page;
  const mirror = data.mirror;
  const rows = page.rows;
  const courses = coursesOf(TOUR, () => undefined);
  return (
    <>
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary" data-tour="summary">
          <span>プロジェクト {rows.length} 件</span>
          <span className="path" title={page.projectsDir}>
            フォルダ: {`${page.projectsRel}/`}
          </span>
          {mirror !== undefined && <CheckedAt mirror={mirror} />}
        </div>
        <div className="controls">
          {mirror !== undefined && <SyncAllButton mirror={mirror} act={act} />}
          <button type="button" className="action" data-action="refresh" data-tour="refresh" onClick={() => act({ type: "refresh" })}>
            更新
          </button>
        </div>
      </ScreenHeader>
      <Banners page={page} act={act} />
      {mirror !== undefined && <MirrorBanners mirror={mirror} />}
      <section className="clone" data-tour="clone">
        <h2>リポジトリを clone する</h2>
        <div className="clone-form">
          <label className="grow" data-tour="clone-url">
            URL{" "}
            <input
              id="url"
              type="text"
              placeholder="https://gitlab.example.com/group/repo.git"
              spellCheck={false}
              value={clone.url}
              onChange={(event) => {
                const url = event.target.value;
                const now = cloneRef.current;
                remember({ ...now, url, name: now.nameTouched ? now.name : guessName(url) });
              }}
            />
          </label>
          <label data-tour="clone-name">
            名前{" "}
            <input
              id="name"
              type="text"
              placeholder="URL から自動で入る"
              spellCheck={false}
              value={clone.name}
              onChange={(event) => {
                const name = event.target.value;
                remember({ ...cloneRef.current, name, nameTouched: name !== "" });
              }}
            />
          </label>
          <button
            type="button"
            className="action primary"
            data-action="clone"
            data-tour="clone-run"
            title="git clone を「ccnavi」ターミナルで実行します。認証が必要ならターミナルで入力してください"
            onClick={() => act({ type: "clone", url: clone.url, name: clone.name })}
          >
            clone
          </button>
        </div>
        <p id="status" className={status === undefined ? "status hidden" : status.kind === "failed" ? "status failed" : "status"}>
          {status?.message ?? ""}
        </p>
      </section>
      <section className="list" data-tour="list">
        <h2>
          ワークスペース内のプロジェクト <span className="count">{rows.length}</span>
        </h2>
        {rows.length === 0 ? (
          <p className="empty">プロジェクトがありません。上のボタンで clone するか、既存のリポジトリを projects/ の直下に移動してください。</p>
        ) : (
          <ul className="projects">
            {rows.map((row, index) => (
              <Project key={row.name} row={row} ticketsEnabled={page.ticketsEnabled} tour={index === 0} mirror={mirror} act={act} />
            ))}
          </ul>
        )}
      </section>
      <Strays strays={page.strays} />
      <section className="workspace" data-tour="workspace">
        <h2>ワークスペース（プロジェクト外）</h2>
        <p className="hint">
          <span className="mono">{page.root}</span>（ワークツリー {page.workspaceWorktrees.length} 件
          {page.workspaceWorktrees.length > 0 && `: ${page.workspaceWorktrees.join(", ")}`}）
        </p>
        <SelfRules page={page} />
      </section>
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className="foot">
        最終更新 {page.generatedAt}（{page.root}）
      </footer>
    </>
  );
}

/**
 * 上部の帯。`.gitignore` の帯（直すボタン付き）と同じ事象は 2 度出さない。
 *
 * 置き場がワークスペースの git の索引に載っている（ぶつかりか載せ忘れ。`isTrackedProjectsDir`）ときは、
 * `.gitignore` の帯も「無視されていない」も出さず、実行ファイルの苦情の帯だけを出す。
 * `.gitignore` に `/projects/` があっても（`page.ignored` が真でも）、索引に載っている限り苦情は出たまま。
 */
function Banners({ page, act }: { readonly page: ProjectsPage; readonly act: (message: Parameters<typeof post>[0]) => void }): JSX.Element {
  const banners: JSX.Element[] = [];
  if (page.lintError !== "") {
    banners.push(
      <div key="lint-error" className="banner warn">
        検証結果を取得できなかったので、プロジェクトごとの結果は出せません。{page.lintError}
      </div>,
    );
  }
  const tracked = page.dirProblems.some((p) => isTrackedProjectsDir(p, page.projectsRel));
  if (!page.ignored && !tracked) {
    banners.push(
      <div key="ignore" className="banner warn">
        <code>.gitignore</code> に <code>/{page.projectsRel}/</code>{" "}
        がありません。各プロジェクトは自分の git リポジトリを持つので、ワークスペースの git からは除外してください。
        <button type="button" className="action" data-action="fix-ignore" onClick={() => act({ type: "fixIgnore" })}>
          .gitignore に追加
        </button>
      </div>,
    );
  }
  const dirProblems = page.dirProblems.filter((p) => (page.ignored && !tracked) || !/無視されていない/.test(p.detail));
  for (const [index, p] of dirProblems.entries()) {
    banners.push(
      <div key={`dir:${index}`} className={`banner ${p.severity}`}>
        {p.severity}: {p.detail}
      </div>,
    );
  }
  return <>{banners}</>;
}

/** ワークスペースの設定のルールの有無。無いのは正常なので warn の色は使わない */
function SelfRules({ page }: { readonly page: ProjectsPage }): JSX.Element {
  return (
    <div className="self-rules">
      <span>ワークスペースの設定のルール</span>{" "}
      {page.selfRulesExists ? <span className="ok">あり</span> : <span className="dim">なし</span>}{" "}
      <span className="mono small">{page.selfRulesRel}</span>
    </div>
  );
}

/** プロジェクトとして認識されない git リポジトリ。表示だけで、操作は付けない */
function Strays({ strays }: { readonly strays: readonly Stray[] }): JSX.Element | null {
  if (strays.length === 0) {
    return null;
  }
  return (
    <section className="strays">
      <h2>
        プロジェクトとして認識されない git リポジトリ <span className="count">{strays.length}</span>
      </h2>
      <p className="hint">
        ワークスペース直下から 2 階層まで（`projects/` の中だけ 3 階層まで）を探して見つかったものです。プロジェクトとして扱うには <code>projects/</code>{" "}
        の直下へ移してください。
      </p>
      <ul className="stray-list">
        {strays.map((s) => (
          <li key={s.path}>
            <span className="mono">{s.path}</span> <span className="dim">{s.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
