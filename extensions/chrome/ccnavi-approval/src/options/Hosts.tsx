/**
 * 通信先の節。足したホストの一覧（外すボタン付き）と、足す欄。
 *
 * 足すときは、そのホスト名への通信を Chrome に許可してもらう（ポートは問わない）（ユーザの操作から呼ぶ。許可しなければ足さない）。
 * 外すときは、そのホストの PAT を消してから、許可も外す。リポジトリが残っているホストは外さない。
 */
import { useEffect, useState, type FormEvent, type JSX } from "react";

import { customHost, originPattern, rowOf } from "../core/custom-hosts.js";
import type { Host } from "../core/hosts.js";
import { ask, loadAllHosts, loadCustomHosts, loadCustomRows, loadRepos, type Said } from "./api.js";
import { Status } from "./Status.js";

const KIND = { github: "GitHub", gitlab: "GitLab" } as const;

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
      const granted = await chrome.permissions.request({ origins: [originPattern(host)] });
      if (!granted) {
        throw new Error(`${host.web} への通信を許可しないと足せない`);
      }
      const all = await loadAllHosts();
      if (all.some((h) => h.id === host.id)) {
        throw new Error(`${host.id} は足してあるか、組み込みの通信先と同じ名前`);
      }
      const rows = await loadCustomRows();
      await chrome.storage.local.set({ customHosts: [...rows, rowOf(host)] });
      setSaid({ text: `${host.web}（${KIND[host.kind]}）を足した。PAT は「PAT」の節で登録する`, ok: true });
      await reload();
      onChange();
    } catch (err) {
      setSaid({ text: (err as Error).message, ok: false });
    }
  };

  const remove = async (h: Host): Promise<void> => {
    const repos = await loadRepos();
    if (repos.some((r) => r.host === h.id)) {
      setSaid({ text: `${h.id} のリポジトリが残っている。先にリポジトリを外してから、ホストを外す`, ok: false });
      return;
    }
    // PAT を先に消す。ホストを外したあとは、そのホストの PAT を使う頼みが届かなくなるため
    const cleared = await ask({ kind: "token.clear", host: h.id });
    if (!cleared.ok) {
      setSaid({ text: cleared.error, ok: false });
      return;
    }
    const rows = (await loadCustomRows()).filter((r) => customHost(r.kind, r.url).id !== h.id);
    await chrome.storage.local.set({ customHosts: rows });
    await chrome.permissions.remove({ origins: [originPattern(h)] });
    setSaid({ text: `${h.id} を外した。PAT も消した。ホスト側でも失効させてください`, ok: true });
    await reload();
    onChange();
  };

  return (
    <section>
      <h2>通信先</h2>
      <ul id="host-list" className="list">
        {hosts.length === 0 ? (
          <li className="row entry">
            <span className="text">足したホストは無い（GitHub.com と GitLab.com は組み込み）</span>
          </li>
        ) : null}
        {hosts.map((h) => (
          <li key={h.id} className="row entry">
            <div className="row-head">
              <span className="text">{`${h.web}（${KIND[h.kind]}）`}</span>
              <button type="button" className="action small" onClick={() => void remove(h)}>
                外す
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
          <span className="cap">ホストの URL（オリジンだけ。例 https://gitlab.example.com・http://localhost:8080）</span>
          <input name="url" id="host-url" type="text" required placeholder="https://gitlab.example.com" />
        </label>
        <div className="buttons">
          <button type="submit" className="action primary">
            足す
          </button>
        </div>
      </form>
      <Status id="host-msg" said={said} />
      <p className="hint">
        足すと、そのホストへの通信を Chrome に許可してもらいます。許可したホスト名にだけ通信し、PAT もそこへだけ送ります。https のホストと、http では localhost と 127.0.0.1 を足せます。GitLab
        は API が「ホスト/api/v4」、GitHub Enterprise Server は「ホスト/api/v3」と「ホスト/api/graphql」にあるものとして扱います。
      </p>
    </section>
  );
}
