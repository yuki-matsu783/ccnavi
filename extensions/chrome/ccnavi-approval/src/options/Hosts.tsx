/**
 * 通信先の節。足したホストの一覧（外すボタン付き）と、足す欄。
 *
 * 足すときは、そのホスト名への通信を Chrome に許可してもらう（ポートは問わない）（ユーザの操作から呼ぶ。許可しなければ足さない）。
 * 外すときは、そのホストの PAT を消してから、許可も外す。リポジトリが残っているホストは外さない。
 */
import { useEffect, useState, type FormEvent, type JSX } from "react";

import { customHost, originPatterns, releasablePatterns, rowId, rowOf } from "../core/custom-hosts.js";
import type { Host } from "../core/hosts.js";
import { ask, loadAllHosts, loadCustomHosts, loadRawCustomRows, loadRepos, type Said } from "./api.js";
import { Status } from "./Status.js";

const KIND = { github: "GitHub", gitlab: "GitLab" } as const;

/** 許可を外す（ほかの通信先が使っていないものだけ）。外せなくても行は外れているので、失敗は言わない（Chrome の設定から外せる） */
async function releasePermission(h: Host, others: readonly Host[]): Promise<void> {
  const origins = releasablePatterns(h, others);
  if (origins.length === 0) return;
  try {
    await chrome.permissions.remove({ origins });
  } catch {
    // 外せなかった許可は Chrome の設定で外せる
  }
}

/** `onChange` は足す・外すのあとに呼ぶ（リポジトリと PAT の節が読み直す） */
export function Hosts({ onChange }: { readonly onChange: () => void }): JSX.Element {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [said, setSaid] = useState<Said | undefined>(undefined);

  const reload = async (): Promise<void> => setHosts(await loadCustomHosts());
  useEffect(() => {
    void reload();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      // 許可の画面はユーザの操作の直後に出す。読み込みはそのあとにする
      const host = customHost(form.get("kind"), form.get("url"));
      const granted = await chrome.permissions.request({ origins: originPatterns(host) });
      if (!granted) {
        throw new Error(`${host.web} への通信を許可しないと追加できません。`);
      }
      const all = await loadAllHosts();
      try {
        if (all.some((h) => h.id === host.id)) {
          throw new Error(`${host.id} は登録済みか、標準の通信先と名前が重複しています。`);
        }
        const rows = await loadRawCustomRows();
        await chrome.storage.local.set({ customHosts: [...rows, rowOf(host)] });
      } catch (err) {
        // 足せなかったら、今の許可で使う通信先が無ければ、いま付けた許可を外す
        await releasePermission(host, all);
        throw err;
      }
      setSaid({ text: `${host.web}（${KIND[host.kind]}）を追加しました。「PAT」欄でトークンを登録してください。`, ok: true });
    } catch (err) {
      setSaid({ text: (err as Error).message, ok: false });
    } finally {
      await reload();
      onChange();
    }
  };

  const remove = async (h: Host): Promise<void> => {
    const repos = await loadRepos();
    if (repos.some((r) => r.host === h.id)) {
      setSaid({ text: `${h.id} にリポジトリが登録されています。先にリポジトリの登録を削除してから、ホストを削除してください。`, ok: false });
      return;
    }
    try {
      // PAT を先に消す。ホストを外したあとは、そのホストの PAT を使う頼みが届かなくなるため
      const cleared = await ask({ kind: "token.clear", host: h.id });
      if (!cleared.ok) {
        setSaid({ text: cleared.error, ok: false });
        return;
      }
      // 読めない行も残す（対象の行だけを外す）
      const rows = (await loadRawCustomRows()).filter((r) => rowId(r) !== h.id);
      await chrome.storage.local.set({ customHosts: rows });
      // 同じ許可を組み込みや別の port の行も使っていれば、許可は外さない
      await releasePermission(h, await loadAllHosts());
      setSaid({ text: `${h.id} と、このブラウザに保存されていた PAT を削除しました。ホスト側でも PAT を失効させてください。`, ok: true });
    } catch (err) {
      setSaid({ text: (err as Error).message, ok: false });
    } finally {
      await reload();
      onChange();
    }
  };

  return (
    <section>
      <h2>通信先</h2>
      <ul id="host-list" className="list">
        {hosts.length === 0 ? (
          <li className="row entry">
            <span className="text">追加のホストは登録されていません（GitHub.com と GitLab.com は標準で利用できます）。</span>
          </li>
        ) : null}
        {hosts.map((h) => (
          <li key={h.id} className="row entry">
            <div className="row-head">
              <span className="text">{`${h.web}（${KIND[h.kind]}）`}</span>
              <button type="button" className="action small" onClick={() => void remove(h)}>
                削除
              </button>
            </div>
          </li>
        ))}
      </ul>
      <form id="host-form" className="form" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span className="cap">種類</span>
          <select name="kind" id="host-kind">
            <option value="gitlab">GitLab</option>
            <option value="github">GitHub（Enterprise Server）</option>
          </select>
        </label>
        <label className="field">
          <span className="cap">ホストの URL（スキーム・ホスト名・必要に応じてポート番号のみ。例 https://gitlab.example.com・http://localhost:8080）</span>
          <input name="url" id="host-url" type="text" required placeholder="https://gitlab.example.com" />
        </label>
        <div className="buttons">
          <button type="submit" className="action primary">
            追加
          </button>
        </div>
      </form>
      <Status id="host-msg" said={said} />
      <p className="hint">
        追加すると、そのホストへの通信の許可を Chrome に求めます。許可したホスト名にのみ通信し、PAT も許可したホストにのみ送信します。追加できるのは https のホストと、http の localhost・127.0.0.1 です。GitLab
        は API が「ホスト/api/v4」、GitHub Enterprise Server は「ホスト/api/v3」と「ホスト/api/graphql」にあるものとして扱います。
        GitHub として登録した *.ghe.com（GHE.com）は、API が「https://api.ホスト」、GraphQL が「https://api.ホスト/graphql」にあるものとして扱い、画面と API の両方の通信を許可してもらいます（ポートは付けられません）。
        種類はここで選んだものが優先されます。
      </p>
    </section>
  );
}
