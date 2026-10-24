/**
 * リポジトリの節。登録した一覧（外すボタン付き）と、登録の欄。
 *
 * 欄は React に値を持たせない（送るときに FormData で読む）。書き換える前の画面と同じ読み方にして、
 * 入れた値の扱い（normalizeRepo）を変えないため。
 */
import { useEffect, useState, type FormEvent, type JSX } from "react";

import type { Host } from "../core/hosts.js";
import { normalizeRepo, repoKey, type RepoConfig } from "../core/settings.js";
import { loadAllHosts, loadRepos, type Said } from "./api.js";
import { Status } from "./Status.js";

function describe(r: RepoConfig): string {
  const integ = r.integration === "" ? "デフォルトブランチ" : r.integration;
  const extra = r.extraBranches.length > 0 ? `・指定 ${r.extraBranches.join(", ")}` : "";
  const proj = r.project ? `・プロジェクト ${r.project}（ワークスペース ${r.workspace}）` : "";
  return `${repoKey(r)}（統合先 ${integ}・直近 ${r.recentDays} 日${extra}${proj}）`;
}

/** `revision` は通信先の一覧が変わった回数。変わったら読み直す（ホストの選び先が変わるため） */
export function Repos({ revision }: { readonly revision: number }): JSX.Element {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [repos, setRepos] = useState<RepoConfig[]>([]);
  const [said, setSaid] = useState<Said | undefined>(undefined);

  const reload = async (): Promise<void> => {
    setHosts(await loadAllHosts());
    setRepos(await loadRepos());
  };
  useEffect(() => {
    void reload();
  }, [revision]);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const repo = normalizeRepo(Object.fromEntries(form.entries()) as Record<string, unknown>, await loadAllHosts());
      const rest = (await loadRepos()).filter((o) => repoKey(o) !== repoKey(repo));
      if (repo.project && !rest.some((o) => repoKey(o) === repo.workspace && !o.project)) {
        throw new Error(`ワークスペースのリポジトリ ${repo.workspace} を先に登録してください`);
      }
      await chrome.storage.local.set({ repos: [...rest, repo] });
      setSaid({ text: `${repoKey(repo)} を登録しました。`, ok: true });
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
                登録を削除
              </button>
            </div>
          </li>
        ))}
      </ul>
      <form id="repo-form" className="form" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span className="cap">ホスト</span>
          <select name="host" id="repo-host">
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>
                {h.id}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="cap">オーナー／グループ</span>
          <input name="owner" type="text" required />
        </label>
        <label className="field">
          <span className="cap">リポジトリ</span>
          <input name="repo" type="text" required />
        </label>
        <label className="field">
          <span className="cap">統合先ブランチ名</span>
          <input name="integration" type="text" placeholder="未入力の場合はデフォルトブランチ" />
        </label>
        <label className="field">
          <span className="cap">表示対象のブランチを検索する期間（過去 N 日）</span>
          <input name="recentDays" type="number" min="0" max="90" defaultValue="7" className="num" />
        </label>
        <label className="field">
          <span className="cap">追加で表示するブランチ（空白またはカンマ区切り）</span>
          <input name="extraBranches" type="text" />
        </label>
        <label className="field">
          <span className="cap">プロジェクト名（ローカルの projects/ 配下のディレクトリ名。未入力の場合はワークスペース本体）</span>
          <input name="project" id="repo-project" type="text" />
        </label>
        <label className="field">
          <span className="cap">ワークスペースのリポジトリ（プロジェクトを登録するとき）</span>
          {/* プロジェクトのリポジトリが選ぶワークスペース（登録したワークスペース自身のリポジトリ） */}
          <select name="workspace" id="repo-workspace">
            <option value="">選択してください</option>
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
        検索期間と追加表示するブランチの設定は、承認待ちのチケットを検索するためにのみ使用します。判定には、統合先ブランチ、親チケットのブランチ、および先行チケットの依存関係をたどった先の親ブランチのみを使用します。表示条件によって承認可否の判定結果が変わることはありません。
      </p>
      <p className="hint">
        {
          "プロジェクトのリポジトリ（ローカルの projects/<名前> にクローンするリポジトリ）を登録するときは、プロジェクト名を入れ、先に登録したワークスペースのリポジトリを選びます。共通レイヤー、設定ファイル、互換性確認用のマーカーは、ワークスペースの統合先から取得します。保存先のパスは既定値（wip/proposals・.ccnavi/approved）に固定されており、.claude/settings.json の env では変更できません。GitLab の入れ子のグループは、「オーナー／グループ」欄に group/sub の形式で入力してください。"
        }
      </p>
    </section>
  );
}
