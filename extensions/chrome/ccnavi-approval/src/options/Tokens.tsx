/**
 * PAT の節。ホストごとの登録の有無と期限、登録・消すの欄、PAT を作るページへのリンク。
 *
 * PAT は service worker に渡して置かせ、この画面では読み返さない（登録済みかと期限だけを聞く）。
 * 期限はホストの応答から読む。読めないときのために、登録のときに日付を入れられる。
 */
import { useEffect, useRef, useState, type FormEvent, type JSX } from "react";

import type { Host } from "../core/hosts.js";
import type { TokenStatus } from "../core/protocol.js";
import { ask, loadAllHosts, type Said } from "./api.js";
import { Status } from "./Status.js";

interface Row {
  readonly host: Host;
  readonly status: TokenStatus | null;
}

function describe({ host, status }: Row): string {
  const set = status?.set === true;
  return `${host.id}（${host.kind === "gitlab" ? "GitLab" : "GitHub"}）：${set ? "登録済み" : "未登録"}${status?.notice ? `・${expiry(status.notice)}` : ""}`;
}

/** 一覧の行に出す期限（文面の全文はボードのバナーとアイコンのツールチップに出す） */
function expiry(notice: NonNullable<TokenStatus["notice"]>): string {
  const day = notice.expiresAt.slice(0, 10);
  if (notice.level === "unknown") return "期限不明";
  if (notice.level === "expired") return `期限切れ（${day}）`;
  if (notice.level === "soon") return `あと ${notice.daysLeft ?? 0} 日で期限切れ（${day}）`;
  return `期限 ${day}`;
}

function createUrl(h: Host): string {
  return h.kind === "github" ? `${h.web}/settings/personal-access-tokens/new` : `${h.web}/-/user_settings/personal_access_tokens`;
}

/** `revision` は通信先の一覧が変わった回数。変わったら読み直す */
export function Tokens({ revision }: { readonly revision: number }): JSX.Element {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [said, setSaid] = useState<Said | undefined>(undefined);
  const hostRef = useRef<HTMLSelectElement>(null);
  const tokenRef = useRef<HTMLInputElement>(null);
  const expiresRef = useRef<HTMLInputElement>(null);

  const reload = async (): Promise<void> => {
    const all = await loadAllHosts();
    const next: Row[] = [];
    for (const host of all) {
      const res = await ask({ kind: "token.status", host: host.id });
      next.push({ host, status: res.ok ? (res.value as TokenStatus) : null });
    }
    setHosts(all);
    setRows(next);
  };
  useEffect(() => {
    void reload();
  }, [revision]);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const host = String(new FormData(event.currentTarget).get("host") ?? "");
    const input = tokenRef.current as HTMLInputElement;
    const expires = expiresRef.current as HTMLInputElement;
    const res = await ask({ kind: "token.set", host, token: input.value.trim(), expires: expires.value });
    input.value = "";
    expires.value = "";
    setSaid({ text: res.ok ? `${host} の PAT を登録しました。差し替えた場合は、古いトークンをホスト側で失効させてください。` : res.error, ok: res.ok });
    await reload();
  };

  const clear = async (): Promise<void> => {
    const host = hostRef.current?.value ?? "";
    if (!window.confirm(`${host} の保存済み PAT をこのブラウザから削除します。削除しますか？`)) return;
    const res = await ask({ kind: "token.clear", host });
    setSaid({ text: res.ok ? `${host} の PAT を削除しました。ホスト側でも失効させてください。` : res.error, ok: res.ok });
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
            {hosts.map((h) => (
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
          <span className="cap">有効期限（ホストから取得できない場合に使用。YYYY-MM-DD）</span>
          <input id="token-expires" name="expires" type="date" ref={expiresRef} />
        </label>
        <div className="buttons">
          <button type="submit" className="action primary">
            登録
          </button>
          <button type="button" id="token-clear" className="action" onClick={() => void clear()}>
            保存済み PAT を削除
          </button>
        </div>
      </form>
      <Status id="token-msg" said={said} />
      <p className="hint">使いみち：承認とレビュー済みを親ブランチに反映するとき、「始める」で Issue から親ブランチを作成するとき、残った指摘の対応方針を確定するときに使います。</p>
      <p className="hint">
        GitHub：fine-grained token を作り、対象のリポジトリを限定して、次の権限を選んでください。Contents: Read and write（ブランチの作成にも使用）・Metadata: Read-only・Pull requests: Read and write（指摘への返信と解決、プルリクエストへのコメント）・Issues: Read and write（issue の作成と検索）。classic の PAT の場合は repo スコープです。
      </p>
      <p className="hint">
        GitLab：api スコープを選んでください（write_repository では Commits API に書き込めません）。指摘を解決するには、Developer 以上のロールが必要です。プロジェクトアクセストークンを作成できる場合は、対象をそのプロジェクトに限定できます。作成できない場合は個人の PAT になり、プロジェクトを限定できません。
      </p>
      <p className="hint">作成時の注意：有効期限は 90 日にし、エージェント用のトークンとは別に作成してください。残った指摘の対応方針を確定するときは、書き込む前に権限を確かめ、足りなければ何も書き込まずに必要な権限の名前を表示して止まります。</p>
      <p className="hint">保存：PAT は、このブラウザのプロファイルに暗号化せずに保存します。同じ OS ユーザで動くほかのプログラムから読み取れるため、リポジトリを限定した最小権限のトークンにしてください。</p>
      <p className="hint">有効期限の通知：有効期限はホストの応答から取得します。取得できない場合は上の欄の日付を使います。有効期限の 7 日前から、ボードと拡張機能のアイコンで通知します。</p>
      <ul id="token-links" className="links">
        {hosts.map((h) => (
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
