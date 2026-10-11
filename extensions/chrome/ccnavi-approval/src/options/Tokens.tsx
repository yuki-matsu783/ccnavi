/**
 * PAT の節。ホストごとの登録の有無と期限、登録・消すの欄、PAT を作るページへのリンク。
 *
 * PAT は service worker に渡して置かせ、この画面では読み返さない（登録済みかと期限だけを聞く）。
 * 期限はホストの応答から読む。読めないときのために、登録のときに日付を入れられる。
 */
import { useEffect, useRef, useState, type FormEvent, type JSX } from "react";

import type { Host } from "../core/hosts.js";
import type { TokenStatus } from "../core/protocol.js";
import { ask, HOSTS, type Said } from "./api.js";
import { Status } from "./Status.js";

interface Row {
  readonly host: Host;
  readonly status: TokenStatus | null;
}

function describe({ host, status }: Row): string {
  const set = status?.set === true;
  const when = status?.notice ? `（${status.notice.text}）` : "";
  return `${host.id}（${host.kind === "gitlab" ? "GitLab" : "GitHub"}）: ${set ? "登録済み" : "未登録"}${when}`;
}

function createUrl(h: Host): string {
  return h.kind === "github" ? `${h.web}/settings/personal-access-tokens/new` : `${h.web}/-/user_settings/personal_access_tokens`;
}

export function Tokens(): JSX.Element {
  const [rows, setRows] = useState<Row[]>([]);
  const [said, setSaid] = useState<Said | undefined>(undefined);
  const hostRef = useRef<HTMLSelectElement>(null);
  const tokenRef = useRef<HTMLInputElement>(null);
  const expiresRef = useRef<HTMLInputElement>(null);

  const reload = async (): Promise<void> => {
    const next: Row[] = [];
    for (const host of HOSTS) {
      const res = await ask({ kind: "token.status", host: host.id });
      next.push({ host, status: res.ok ? (res.value as TokenStatus) : null });
    }
    setRows(next);
  };
  useEffect(() => {
    void reload();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const host = String(new FormData(event.currentTarget).get("host") ?? "");
    const input = tokenRef.current as HTMLInputElement;
    const expires = expiresRef.current as HTMLInputElement;
    const res = await ask({ kind: "token.set", host, token: input.value.trim(), expires: expires.value });
    input.value = "";
    expires.value = "";
    setSaid({ text: res.ok ? `${host} の PAT を登録した。差し替えた場合は、古いトークンをホスト側で失効させてください` : res.error, ok: res.ok });
    await reload();
  };

  const clear = async (): Promise<void> => {
    const host = hostRef.current?.value ?? "";
    const res = await ask({ kind: "token.clear", host });
    setSaid({ text: res.ok ? `${host} の PAT を消した。ホスト側でも失効させてください` : res.error, ok: res.ok });
    await reload();
  };

  return (
    <section>
      <h2>PAT</h2>
      <ul id="token-list" className="list">
        {rows.map((row) => (
          <li key={row.host.id} className="row entry">
            <div className="row-head">
              <span className="text">{describe(row)}</span>
            </div>
          </li>
        ))}
      </ul>
      <form id="token-form" className="form" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span className="cap">ホスト</span>
          <select name="host" id="token-host" ref={hostRef}>
            {HOSTS.map((h) => (
              <option key={h.id} value={h.id}>
                {h.id}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="cap">PAT</span>
          <input id="token-value" type="password" autoComplete="off" required ref={tokenRef} />
        </label>
        <label className="field">
          <span className="cap">期限（ホストの応答から読めないときに使う。YYYY-MM-DD）</span>
          <input id="token-expires" name="expires" type="date" ref={expiresRef} />
        </label>
        <div className="buttons">
          <button type="submit" className="action primary">
            登録
          </button>
          <button type="button" id="token-clear" className="action" onClick={() => void clear()}>
            消す
          </button>
        </div>
      </form>
      <Status id="token-msg" said={said} />
      <p className="hint">
        PAT は、承認・取り下げ・レビュー済みを親のブランチへ書くのと、「始める」で issue から親のブランチを作るのに使います。GitHub では fine-grained token を作り、対象のリポジトリを絞って、Contents: Read and write（ブランチを作るのにも使う）・Metadata: Read-only・Pull requests: Read-only・Issues: Read-only を選びます。GitLab では api スコープを選びます（Commits API は write_repository では書けないため）。project access token が使えればプロジェクトを絞れます。使えなければ個人の PAT になり、プロジェクトは絞れません。期限は 90 日を選びます。エージェント用のトークンとは別に作ります。PAT はこのブラウザのプロファイルに平文で置かれ、同じ OS ユーザーのプロセスからは読めます。リポジトリを絞った最小の権限のトークンにしてください。期限はホストの応答から読みます。読めないときは上の欄の日付を使い、切れる 7 日前からボードとアイコンで知らせます。
      </p>
      <ul id="token-links" className="links">
        {HOSTS.map((h) => (
          <li key={h.id}>
            <a href={createUrl(h)} target="_blank" rel="noopener noreferrer">
              {h.id} で PAT を作る
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
