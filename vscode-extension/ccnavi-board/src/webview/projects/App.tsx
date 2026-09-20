/**
 * プロジェクト管理画面の本体。帯・clone の欄・プロジェクトのカード・認識されない git・ワークスペース本体。
 *
 * 見せる中身は拡張ホストが渡す（`ProjectsData`）。画面が自分で持つのは、人が触って決めるもの
 * （clone の欄、どのメニューを開いているか、直前の操作の一言）だけ。clone も書き込みも画面はしない。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { CloneStatus, ProjectsData, ProjectsPage, Stray, ToProjects } from "../../core/projects-view.js";
import { applyAppearance } from "../appearance.js";
import { MENU_KINDS, menuId } from "./Menu.js";
import { Project } from "./Project.js";
import { post } from "./post.js";
import { EMPTY, guessName, loadClone, saveClone, type CloneState } from "./state.js";

export function App({ initial }: { readonly initial: ProjectsData }): JSX.Element {
  const [data, setData] = useState<ProjectsData>(initial);
  const [clone, setClone] = useState<CloneState>(loadClone);
  /**
   * 直前の操作の一言。生きている画面にしか届かないので、持ち越さない（拡張ホストも覚えない）。
   *
   * 消える条件は 2 つ。**失敗（`failed`）は次の一覧が届いたら消す。** 一覧が新しくなった後も
   * 「プロジェクト X が一覧に無い」が赤く残ると、人はいまも失敗していると読む。
   * 案内（`info`）は残す。clone を送った直後は `.git` の出現で必ず読み直しが走るので、
   * ここで消すと案内が一瞬で消える（移行前がそれだった）
   */
  const [status, setStatus] = useState<CloneStatus | undefined>(undefined);
  const [openMenu, setOpenMenu] = useState<string | undefined>(undefined);

  // 受け口（メッセージ）は描くたびに作り直さない。打ちかけの欄を消すのに今の値が要るので写しておく
  const cloneRef = useRef<CloneState>(clone);
  cloneRef.current = clone;

  const remember = (next: CloneState): void => {
    setClone(next);
    saveClone(next);
  };

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToProjects>;
      if (message.type === "data" && message.data !== undefined) {
        setData(message.data);
        // 開いていたメニューの持ち主が一覧から消えていたら閉じる。残すと、同じ名前で
        // 戻ってきたときに押していないメニューが開いた状態で出る。
        // 一致は `menuId` が組んだ綴りそのもので見る（前方一致だと、`:` を含む名前の
        // メニューを、その接頭辞になっている別のプロジェクトが自分のものだと言い出す）
        const rows = message.data.kind === "page" ? message.data.page.rows : [];
        const alive = new Set(rows.flatMap((r) => MENU_KINDS.map((kind) => menuId(r.name, kind))));
        setOpenMenu((now) => (now !== undefined && !alive.has(now) ? undefined : now));
        // 一覧が新しくなったので、それを見て言った失敗はもう今のことではない
        setStatus((now) => (now?.kind === "failed" ? undefined : now));
      } else if (message.type === "failed") {
        setStatus({ kind: "failed", message: String(message.message ?? "") });
      } else if (message.type === "info") {
        setStatus({ kind: "info", message: String(message.message ?? "") });
      } else if (message.type === "cloned") {
        // clone をターミナルへ送った。同じものをもう一度送らないよう、打ち込んだ URL と名前を消す
        setClone(EMPTY);
        saveClone(EMPTY);
        setStatus({ kind: "info", message: String(message.message ?? "") });
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。裏に回って捨てられた画面は、表に戻ると拡張ホストが入れてある HTML から
    // 作り直される。その HTML は少し古いことがあるので、いまの中身をもらい直す
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // メニューは 1 つだけ開く。外を押すか Esc で閉じる（項目を押したときは Project が閉じる）
  useEffect(() => {
    const onDown = (event: Event): void => {
      const target = event.target as Element | null;
      const inside = typeof target?.closest === "function" ? target.closest("details.menu") : null;
      if (inside === null) {
        setOpenMenu(undefined);
      }
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setOpenMenu(undefined);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">プロジェクトの一覧を読み直せなかった。原因を直してから「ccnavi ボード: プロジェクト管理を開く」を実行し直す。</p>
        <pre className="load-error">{data.error}</pre>
      </>
    );
  }

  const page = data.page;
  return (
    <>
      <header className="toolbar">
        <div className="summary">
          <span>プロジェクト {page.rows.length} 件</span>
          <span className="path" title={page.projectsDir}>
            置き場: {page.projectsRel === "" ? "（無効）" : `${page.projectsRel}/`}
          </span>
        </div>
        <div className="controls">
          <button
            type="button"
            className="action"
            data-action="open-rules"
            data-name=""
            title="共通層のルール（どのツリーにも効く。既定 .ccnavi/common/rules.yml）を編集し、判定を試す"
            onClick={() => post({ type: "openRules", name: "" })}
          >
            ルール管理
          </button>
          {page.ticketsEnabled && (
            <button type="button" className="action" data-action="open-board" data-name="*" onClick={() => post({ type: "openBoard", name: "*" })}>
              チケット管理
            </button>
          )}
          <button type="button" className="action" data-action="refresh" onClick={() => post({ type: "refresh" })}>
            更新
          </button>
        </div>
      </header>
      <Banners page={page} />
      <section className="clone">
        <h2>リポジトリを clone する</h2>
        <div className="clone-form">
          <label className="grow">
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
          <label>
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
            title="git clone を「ccnavi」ターミナルで実行する。認証が要るならターミナルで入れる"
            onClick={() => post({ type: "clone", url: clone.url, name: clone.name })}
          >
            clone
          </button>
        </div>
        <p id="status" className={status === undefined ? "status hidden" : status.kind === "failed" ? "status failed" : "status"}>
          {status?.message ?? ""}
        </p>
      </section>
      <section className="list">
        <h2>
          ワークスペース内のプロジェクト <span className="count">{page.rows.length}</span>
        </h2>
        {page.rows.length === 0 ? (
          <p className="empty">プロジェクトはまだ無い。上の欄から clone するか、既存のリポジトリを置き場（無ければ作る）の直下へ移す</p>
        ) : (
          <ul className="projects">
            {page.rows.map((row) => (
              <Project key={row.name} row={row} ticketsEnabled={page.ticketsEnabled} openMenu={openMenu} onOpenMenu={setOpenMenu} />
            ))}
          </ul>
        )}
      </section>
      <Strays strays={page.strays} />
      <section className="workspace">
        <h2>ワークスペース本体</h2>
        <p className="hint">
          <span className="mono">{page.root}</span>（ワークツリー {page.workspaceWorktrees.length} 件
          {page.workspaceWorktrees.length > 0 && `: ${page.workspaceWorktrees.join(", ")}`}）
        </p>
        <SelfRules page={page} />
      </section>
      <footer className="foot">
        最終更新 {page.generatedAt}（{page.root}）
      </footer>
    </>
  );
}

/**
 * 上部の帯。置き場が無効なら他の苦情は読む意味が無いので、そこで切る。
 * `.gitignore` の帯（直すボタン付き）と同じ事象は 2 度出さない。
 */
function Banners({ page }: { readonly page: ProjectsPage }): JSX.Element {
  const banners: JSX.Element[] = [];
  if (page.lintError !== "") {
    banners.push(
      <div key="lint-error" className="banner warn">
        検証結果を取得できなかったので、プロジェクトごとの検証結果は出せない。{page.lintError}
      </div>,
    );
  }
  if (page.projectsRel === "") {
    banners.push(
      <div key="no-dir" className="banner warn">
        置き場が無効（CCNAVI_PROJECTS が空）。clone してもプロジェクトとして扱われない
      </div>,
    );
    return <>{banners}</>;
  }
  if (!page.ignored) {
    banners.push(
      <div key="ignore" className="banner warn">
        <code>.gitignore</code> に <code>/{page.projectsRel}/</code>{" "}
        が無い。各プロジェクトは自分の git リポジトリを持つので、ワークスペースの git からは除外する。
        <button type="button" className="action" data-action="fix-ignore" onClick={() => post({ type: "fixIgnore" })}>
          .gitignore に追加
        </button>
      </div>,
    );
  }
  const dirProblems = page.dirProblems.filter((p) => page.ignored || !/無視されていない/.test(p.detail));
  for (const [index, p] of dirProblems.entries()) {
    banners.push(
      <div key={`dir:${index}`} className={`banner ${p.severity}`}>
        {p.severity}: {p.detail}
      </div>,
    );
  }
  return <>{banners}</>;
}

/**
 * ワークスペース自身の層のルール。無いのは正常なので warn の色は使わない。
 * フェーズの種類の行は、チケット制御が disable なら出さない（種類はチケットにしか読まれない）。
 */
function SelfRules({ page }: { readonly page: ProjectsPage }): JSX.Element {
  return (
    <>
      <div className="self-rules">
        <span>自身の層のルール</span>{" "}
        {page.selfRulesExists ? (
          <>
            <span className="ok">あり</span> <span className="mono small">{page.selfRulesRel}</span>
          </>
        ) : (
          <>
            <span className="dim">なし</span> <span className="mono small">{page.selfRulesRel}</span>{" "}
            <button
              type="button"
              className="action small"
              data-action="create-self-rules"
              title="共通層の rules.yml を自身の層にコピーする。文面の sh のパスは {root} 付きに置き換える"
              onClick={() => post({ type: "createSelfRules" })}
            >
              共通層からコピー
            </button>
          </>
        )}{" "}
        <button
          type="button"
          className="action small"
          data-action="open-self-rules"
          disabled={!page.selfRulesExists}
          title="ワークスペース自身のツリーへの書き込みと、全ツリーの Bash に足して当たるルールを編集し、判定を試す"
          onClick={() => post({ type: "openSelfRules" })}
        >
          ルール管理
        </button>
      </div>
      {page.ticketsEnabled && (
        <div className="self-rules">
          <span>自身の層のフェーズの種類</span>{" "}
          <button
            type="button"
            className="action small"
            data-action="open-self-phases"
            title="ワークスペース自身のチケット（project: が空）の計画に、共通層に足して使う種類を編集する。無ければ画面から作れる"
            onClick={() => post({ type: "openSelfPhases" })}
          >
            フェーズ管理
          </button>
        </div>
      )}
    </>
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
        ワークスペース直下から 2 階層までを探して見つかったもの（node_modules、.venv、.claude の中は探さない）。プロジェクトとして扱うには <code>projects/</code>{" "}
        の直下へ移す。この画面からは操作できない。
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
