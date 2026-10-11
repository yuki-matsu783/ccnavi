/**
 * リポジトリの節。登録した一覧（外すボタン付き）と、登録の欄。
 *
 * 欄は React に値を持たせない（送るときに FormData で読む）。書き換える前の画面と同じ読み方にして、
 * 入れた値の扱い（normalizeRepo）を変えないため。
 */
import { useEffect, useState, type FormEvent, type JSX } from "react";

import { normalizeRepo, repoKey, type RepoConfig } from "../core/settings.js";
import { HOSTS, loadRepos, type Said } from "./api.js";
import { Status } from "./Status.js";

function describe(r: RepoConfig): string {
  const integ = r.integration === "" ? "デフォルトブランチ" : r.integration;
  const extra = r.extraBranches.length > 0 ? `・指定 ${r.extraBranches.join(", ")}` : "";
  const proj = r.project ? `・プロジェクト ${r.project}（ワークスペース ${r.workspace}）` : "";
  return `${repoKey(r)}（統合先 ${integ}・直近 ${r.recentDays} 日${extra}${proj}）`;
}

export function Repos(): JSX.Element {
  const [repos, setRepos] = useState<RepoConfig[]>([]);
  const [said, setSaid] = useState<Said | undefined>(undefined);

  const reload = async (): Promise<void> => setRepos(await loadRepos());
  useEffect(() => {
    void reload();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const repo = normalizeRepo(Object.fromEntries(form.entries()) as Record<string, unknown>, HOSTS);
      const rest = (await loadRepos()).filter((o) => repoKey(o) !== repoKey(repo));
      if (repo.project && !rest.some((o) => repoKey(o) === repo.workspace && !o.project)) {
        throw new Error(`ワークスペースのリポジトリ ${repo.workspace} を先に登録してください`);
      }
      await chrome.storage.local.set({ repos: [...rest, repo] });
      setSaid({ text: `${repoKey(repo)} を登録した`, ok: true });
      await reload();
    } catch (err) {
      setSaid({ text: (err as Error).message, ok: false });
    }
  };

  const remove = async (r: RepoConfig): Promise<void> => {
    const rest = (await loadRepos()).filter((o) => repoKey(o) !== repoKey(r));
    await chrome.storage.local.set({ repos: rest });
    await reload();
  };

  return (
    <section>
      <h2>リポジトリ</h2>
      <ul id="repo-list" className="list">
        {repos.map((r) => (
          <li key={repoKey(r)} className="row entry">
            <div className="row-head">
              <span className="text">{describe(r)}</span>
              <button type="button" className="action small" onClick={() => void remove(r)}>
                外す
              </button>
            </div>
          </li>
        ))}
      </ul>
      <form id="repo-form" className="form" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span className="cap">ホスト</span>
          <select name="host" id="repo-host">
            {HOSTS.map((h) => (
              <option key={h.id} value={h.id}>
                {h.id}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="cap">owner</span>
          <input name="owner" type="text" required />
        </label>
        <label className="field">
          <span className="cap">リポジトリ</span>
          <input name="repo" type="text" required />
        </label>
        <label className="field">
          <span className="cap">統合先の名前</span>
          <input name="integration" type="text" placeholder="空ならデフォルトブランチ" />
        </label>
        <label className="field">
          <span className="cap">直近の日数（表示用）</span>
          <input name="recentDays" type="number" min="0" max="90" defaultValue="7" className="num" />
        </label>
        <label className="field">
          <span className="cap">指定のブランチ（表示用。空白かカンマで区切る）</span>
          <input name="extraBranches" type="text" />
        </label>
        <label className="field">
          <span className="cap">プロジェクト名（手元の projects/ の下の名前。空ならワークスペース自身）</span>
          <input name="project" id="repo-project" type="text" />
        </label>
        <label className="field">
          <span className="cap">ワークスペースのリポジトリ（プロジェクトを登録するとき）</span>
          {/* プロジェクトのリポジトリが選ぶワークスペース（登録したワークスペース自身のリポジトリ） */}
          <select name="workspace" id="repo-workspace">
            <option value="">（選ぶ）</option>
            {repos
              .filter((r) => !r.project)
              .map((r) => (
                <option key={repoKey(r)} value={repoKey(r)}>
                  {repoKey(r)}
                </option>
              ))}
          </select>
        </label>
        <div className="buttons">
          <button type="submit" className="action primary">
            登録
          </button>
        </div>
      </form>
      <Status id="repo-msg" said={said} />
      <p className="hint">
        直近の日数と指定のブランチは、承認待ちを見つけるのに使うだけです。判定には、統合先と親のブランチ、先行をたどって行き着く親のブランチだけを入れます。見せ方の設定で判定の答えが変わらないようにするためです。
      </p>
      <p className="hint">
        {
          "プロジェクトのリポジトリ（手元で projects/<名前> に clone するもの）を登録するときは、プロジェクト名を入れ、先に登録したワークスペースのリポジトリを選びます。共通レイヤー・設定・互換のマーカーはワークスペースの統合先から読みます。置き場のパスは既定（wip/proposals・.ccnavi/approved）に固定で、.claude/settings.json の env では変えられません。GitLab の入れ子のグループは owner に group/sub と書きます。"
        }
      </p>
    </section>
  );
}
