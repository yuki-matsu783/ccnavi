/**
 * ボードの状態と副作用。読み直し・書く操作・要確認の読み書き・直前の操作の一言・PAT の期限の帯・自動の読み直しを持つ。
 * 描くのは呼び手（`App.tsx`）で、ここは `document` にも `chrome` にも直接触らない。外界との接点
 * （service worker への依頼・`chrome.storage.local`・確認のダイアログ・Worker・blob のキャッシュ・時計とタイマー）は
 * `BoardEnv` で受け取り、Node の単体試験で差し替える。
 *
 * 外へは、いまの状態（`snapshot()`。変わるたびに作り直す読み取り専用の値）と、変化の通知（`subscribe`）を出す。
 * React の `useSyncExternalStore(subscribe, snapshot)` でそのまま読める形にしてある。
 */
import type { TokenStatus, Response } from "../core/protocol.js";
import type { Host } from "../core/hosts.js";
import type { Issue } from "../core/github.js";
import { startAutoRefresh, type AutoRefresh, type AutoRefreshEnv } from "../core/autorefresh.js";
import { readRepos, repoKey, saveRecentDays, type RepoConfig, type StorageArea } from "../core/settings.js";
import { collectRepo, type BlobCache, type Deps, type FamilyBoard, type HostCall, type RepoBoard, type Stats } from "../core/snapshot.js";
import { listIssues, startIssue } from "../core/start.js";
import { approveFamily, confirmPhase, type Outcome, type WriteDeps } from "../core/write.js";
import type { PyWorker } from "./py-client.js";

/** 親子のチケットごとの「要確認」（リポジトリの鍵 → 親のブランチ名 → 文面）。`chrome.storage.local` の `attention` */
export type Attention = Record<string, Record<string, string>>;

/** 読んだ issue の一覧（「始める」） */
export interface IssueList {
  readonly list: readonly Issue[] | null;
  readonly error: string;
}

/** ボタンを押したときの動き。ボードの欄（`Repo.tsx`）に渡す */
export interface Actions {
  approve(repo: RepoBoard, family: FamilyBoard): void;
  /** レビュー済みにする。渡さなければ「レビュー済みにする」を出さない */
  review?(repo: RepoBoard, family: FamilyBoard, phase: number): void;
  /** issue の一覧を読む（「始める」）。渡さなければ「始める」の欄を出さない */
  loadIssues?(repo: RepoBoard): void;
  /** issue から親のブランチを作る */
  start?(repo: RepoBoard, issue: Issue): void;
  /** 直近の日数を変える（入力欄の文字列のまま渡す。検査と保存は呼び手）。渡さなければ入力欄を出さない */
  setRecentDays?(repo: RepoBoard, value: string): void;
  /** 「要確認」を外す（ユーザが確かめた） */
  dismiss?(repo: RepoBoard, family: string): void;
}

/** 1 つのリポジトリの欄を描く材料。`Repo.tsx` に渡す値そのもの。描き直すときだけ作り直す */
export interface Section {
  readonly board: RepoBoard;
  readonly attention: Readonly<Record<string, string>>;
  readonly issues: IssueList | undefined;
}

/** 直前の操作の一言（`#result`）。`kind` は `data-kind`、`className` は最後に決めた見た目（読み直しで一言を消すときは前のまま） */
export interface ResultLine {
  readonly kind: string | undefined;
  readonly className: string;
  readonly text: string;
}

/** 読み込みの具合（`#status`）。Pyodide の起動の時間は、起動した後だけ持つ */
export interface StatusLine {
  readonly text: string;
  readonly bootMs?: number;
  readonly importMs?: number;
  readonly violations?: number;
}

/** PAT の期限の帯の 1 行 */
export interface BannerLine {
  readonly level: string;
  readonly className: string;
  readonly text: string;
}

export type BoardState = "loading" | "writing" | "done" | "error";

export interface BoardSnapshot {
  /** 登録したリポジトリ（設定画面の値） */
  readonly repos: readonly RepoConfig[];
  /** リポジトリごとの最後のボード */
  readonly boards: ReadonlyMap<string, RepoBoard>;
  /** リポジトリごとの読んだ issue の一覧 */
  readonly issues: ReadonlyMap<string, IssueList>;
  /** 最後に読んだ要確認 */
  readonly attention: Attention;
  /** 画面に出す欄（並べる順）。読み直しの途中は、読めた分だけ */
  readonly sections: ReadonlyMap<string, Section>;
  readonly banner: readonly BannerLine[];
  readonly status: StatusLine;
  readonly result: ResultLine;
  /** 書く操作の最中 */
  readonly busy: boolean;
  /** `body` の `data-state` */
  readonly state: BoardState;
}

/** 書く・読む中身（試験で差し替える。既定は src/core の実物） */
export interface BoardOps {
  readonly collectRepo: typeof collectRepo;
  readonly approveFamily: typeof approveFamily;
  readonly confirmPhase: typeof confirmPhase;
  readonly listIssues: typeof listIssues;
  readonly startIssue: typeof startIssue;
  readonly saveRecentDays: typeof saveRecentDays;
}

export interface BoardEnv {
  readonly hosts: readonly Host[];
  readonly version: string;
  /** service worker へ頼む（`chrome.runtime.sendMessage`） */
  readonly send: (message: unknown) => Promise<unknown>;
  /** `chrome.storage.local` */
  readonly storage: StorageArea;
  /** 設定画面を開く（`chrome.runtime.openOptionsPage`） */
  readonly openOptions: () => Promise<void> | void;
  /** `window.confirm` */
  readonly confirm: (message: string) => boolean;
  readonly startWorker: () => PyWorker;
  readonly blobCache: () => Promise<BlobCache>;
  readonly now: () => Date;
  /** ボードの入力欄を触っている（自動の読み直しを飛ばす） */
  readonly editing: () => boolean;
  /** 自動の読み直しのタブの可視性とタイマー */
  readonly timers: Pick<AutoRefreshEnv, "doc" | "now" | "setTimeout" | "clearTimeout" | "intervalMs">;
  readonly ops?: Partial<BoardOps>;
}

export interface BoardController {
  snapshot(): BoardSnapshot;
  subscribe(listener: () => void): () => void;
  /** ボードの欄（`Repo.tsx`）に渡す操作 */
  readonly actions: Actions;
  /**
   * ボードを読み直す。`fromTimer` が真（自動）のときは、走っていれば何もせず、
   * 操作の結果の表示を消さず、ボードを空にせず（リポジトリごとに差し替える）、描き直す間も入力欄を残す。
   */
  refresh(clearResult?: boolean, fromTimer?: boolean): Promise<void>;
  openOptions(): void;
  /** 境界の外で描画が落ちたとき、`#status` に言って `data-state` を error にする（`main.tsx` が React の `onUncaughtError` から呼ぶ） */
  reportRenderError(err: unknown): void;
  /** 最初の読み込みと自動の読み直しを始める */
  start(): void;
}

const DEFAULT_OPS: BoardOps = { collectRepo, approveFamily, confirmPhase, listIssues, startIssue, saveRecentDays };

function newStats(): Stats {
  return { rest: 0, graphql: 0, blobsFetched: 0, blobsCached: 0 };
}

/** 書いた結果の一言。`kind` は `data-kind` に出す */
export function sayOutcome(outcome: Outcome, what: string): ResultLine {
  const text =
    outcome.kind === "written"
      ? `${what}を反映しました（コミット：${outcome.oid.slice(0, 7)}${outcome.rounds > 1 ? `。途中で親のブランチの先頭が動いたので、${outcome.rounds} 回目で反映しました` : ""}）。反映後の内容も確認しました。`
      : outcome.kind === "changed"
        ? `${what}は反映されていません：${outcome.message}`
        : outcome.kind === "conflict"
          ? `${what}を反映できませんでした。自動再試行は行いません：${outcome.message}`
          : outcome.kind === "attention"
            ? `${what}は確認が必要な状態です。反映されたかどうかと残っている内容を、ホストの履歴で確認してください：${outcome.message}`
            : `${what}は反映されていません：${outcome.message}`;
  return { kind: outcome.kind, className: `notice ${outcome.kind === "written" ? "ok" : "error"}`, text };
}

export function createBoardController(env: BoardEnv): BoardController {
  const ops: BoardOps = { ...DEFAULT_OPS, ...env.ops };
  let snap: BoardSnapshot = {
    repos: [],
    boards: new Map(),
    issues: new Map(),
    attention: {},
    sections: new Map(),
    banner: [],
    status: { text: "読み込み中…" },
    result: { kind: undefined, className: "", text: "" },
    busy: false,
    state: "loading",
  };
  const listeners = new Set<() => void>();
  let worker: PyWorker | null = null;
  /** 自動の読み直しの間隔ごとの管理（`refresh` が終わるたびに数え直す） */
  let auto: AutoRefresh | null = null;
  /** 走っている読み直し。重ねて走らせない */
  let inflight: Promise<void> | null = null;

  function set(patch: Partial<BoardSnapshot>): void {
    snap = { ...snap, ...patch };
    for (const l of [...listeners]) l();
  }

  function setResult(result: Partial<ResultLine>): void {
    set({ result: { ...snap.result, ...result } });
  }

  /** 1 つのリポジトリの欄を描き直す（無ければ末尾に足す） */
  function drawRepo(board: RepoBoard, attention: Attention): void {
    const key = repoKey(board.repo);
    const sections = new Map(snap.sections);
    sections.set(key, { board, attention: attention[key] ?? {}, issues: snap.issues.get(key) });
    set({ sections });
  }

  function hostCall(host: string, stats: Stats): HostCall {
    return async (op, args) => {
      const res = (await env.send({ kind: "host", host, op, args })) as Response;
      if (!res.ok) throw Object.assign(new Error(res.error), { status: res.status });
      if (res.counter) {
        stats.rest += res.counter.rest;
        stats.graphql += res.counter.graphql;
      }
      return res.value;
    };
  }

  function pyWorker(): PyWorker {
    if (worker === null) worker = env.startWorker();
    return worker;
  }

  /** プロジェクトのリポジトリのワークスペース（登録したもの）とそのホストへ頼む関数 */
  function workspaceOf(repo: RepoConfig, stats: Stats): Deps["workspace"] {
    if (!repo.project) return undefined;
    const ws = snap.repos.find((r) => repoKey(r) === repo.workspace && !r.project);
    return ws ? { repo: ws, call: hostCall(ws.host, stats) } : undefined;
  }

  async function writeDeps(repo: RepoConfig): Promise<WriteDeps> {
    const stats = newStats();
    const w = pyWorker();
    return {
      call: hostCall(repo.host, stats),
      py: w.call,
      cache: await env.blobCache(),
      now: env.now,
      stats,
      version: env.version,
      workspace: workspaceOf(repo, stats),
      kind: env.hosts.find((h) => h.id === repo.host)?.kind ?? "github",
    };
  }

  async function readAttention(): Promise<Attention> {
    const got = await env.storage.get("attention");
    const v = got.attention;
    return v && typeof v === "object" ? (v as Attention) : {};
  }

  /** 「要確認」の親子のチケットへは、このブラウザから書かない（ボタンを出さないのに加えて、押す前にも見る） */
  async function blocked(repo: RepoConfig, family: string): Promise<boolean> {
    const why = (await readAttention())[repoKey(repo)]?.[family];
    if (!why) return false;
    setResult({ kind: "refused", className: "notice error", text: `${family} は確認が必要な状態のままです。ホストの履歴を確認し、「確認済みとして警告を解除」を押すまで、このブラウザからは変更を反映しません。` });
    return true;
  }

  /** 要確認になった親子のチケットを `chrome.storage.local` に残し、ユーザが確かめて外すまでボードに出す。要確認になるのは、元に戻すコミット（revert）を積んでも競合が収まらないときや、書けたかを確かめられないときなど（8.4） */
  async function noteAttention(repo: RepoConfig, family: string, outcome: Outcome): Promise<void> {
    if (outcome.kind !== "attention") return;
    const all = await readAttention();
    const key = repoKey(repo);
    all[key] = { ...(all[key] ?? {}), [family]: outcome.message };
    await env.storage.set({ attention: all });
  }

  function say(outcome: Outcome, what: string): void {
    set({ result: sayOutcome(outcome, what) });
  }

  /** PAT の期限の帯。PAT の無いホストは出さない */
  async function drawBanner(repos: readonly RepoConfig[]): Promise<void> {
    set({ banner: [] });
    const lines: BannerLine[] = [];
    for (const host of new Set(repos.map((r) => r.host))) {
      const res = (await env.send({ kind: "token.status", host })) as Response;
      const st = res.ok ? (res.value as TokenStatus) : null;
      if (!st?.set || !st.notice || st.notice.level === "ok") continue;
      lines.push({ level: st.notice.level, className: `notice ${st.notice.level === "expired" ? "error" : "warn"}`, text: st.notice.text });
      set({ banner: [...lines] });
    }
  }

  /** 書く前に、ホストの Approve が外れうるかを確かめる */
  async function approvalWarning(deps: WriteDeps, repo: RepoConfig, family: string): Promise<string> {
    try {
      const prs = (await deps.call("pullApprovals", [repo.owner, repo.repo, family])) as { number: number }[];
      if (prs.length > 0) return `\n\n注意：マージリクエスト ${prs.map((p) => `#${p.number}`).join(", ")} に Approve が付いています。このコミットを反映すると、その Approve が解除されることがあります。`;
    } catch {
      // 読めなくても止めない（注意の表示だけ）
    }
    return "";
  }

  /** 書く操作を 1 つずつ走らせる。書いた（書こうとした）ときだけ、ボードを読み直して描き直す */
  async function act(fn: () => Promise<boolean>): Promise<void> {
    if (snap.busy) return;
    set({ busy: true, state: "writing" });
    let tried = true;
    try {
      tried = await fn();
    } catch (err) {
      // 投げて終わったら失敗の見た目にする（前のまま残すと、直前の成功の緑で失敗を言ってしまう）
      set({ result: { kind: "failed", className: "notice error", text: `反映できませんでした：${(err as Error).message ?? String(err)}` } });
    } finally {
      if (tried) {
        set({ busy: false });
        await refresh(false);
      } else {
        set({ busy: false, state: "done" });
      }
    }
  }

  const actions: Actions = {
    approve(repoBoard, family) {
      void act(async () => {
        if (await blocked(repoBoard.repo, family.family.name)) return false;
        const r = family.result;
        if (!r?.digest || !r.batch) return false;
        const deps = await writeDeps(repoBoard.repo);
        const ids = r.batch.map((e) => e.ticket);
        const warn = await approvalWarning(deps, repoBoard.repo, family.family.name);
        if (!env.confirm(`${family.family.name} に、チケット ${ids.join(", ")} の承認を1つのコミットとして反映します。承認しますか？${warn}`)) return false;
        const outcome = await ops.approveFamily(repoBoard.repo, family.family.name, { ids, digest: r.digest, only: r.only ?? null }, deps);
        await noteAttention(repoBoard.repo, family.family.name, outcome);
        say(outcome, "承認");
        return true;
      });
    },
    review(repoBoard, family, phase) {
      void act(async () => {
        if (await blocked(repoBoard.repo, family.family.name)) return false;
        const deps = await writeDeps(repoBoard.repo);
        const warn = await approvalWarning(deps, repoBoard.repo, family.family.name);
        const msg = `${family.family.name} のフェーズ ${phase} をレビュー済みにする。レビュー待ちの子チケットを done/ に移動し、レビュー済みマーカーとともに1つのコミットとして反映します。反映する前に、押した時点のスレッドとレビューを再取得して確認します。${warn}`;
        if (!env.confirm(msg)) return false;
        const outcome = await ops.confirmPhase(repoBoard.repo, family.family.name, phase, deps);
        await noteAttention(repoBoard.repo, family.family.name, outcome);
        say(outcome, "レビュー済み");
        return true;
      });
    },
    loadIssues(repoBoard) {
      void (async () => {
        const key = repoKey(repoBoard.repo);
        let got: IssueList;
        try {
          got = { list: await ops.listIssues(repoBoard.repo, await writeDeps(repoBoard.repo)), error: "" };
        } catch (err) {
          got = { list: null, error: `Issue一覧を取得できませんでした：${(err as Error).message ?? String(err)}` };
        }
        set({ issues: new Map(snap.issues).set(key, got) });
        drawRepo(snap.boards.get(key) ?? repoBoard, await readAttention());
      })();
    },
    start(repoBoard, issue) {
      void act(async () => {
        if (!env.confirm(`Issue #${issue.number} から作業を始めます。issue に紐づく開いたマージリクエストがあれば、その元ブランチを採用します（ブランチもマージリクエストも作りません）。無ければ、統合先 ${repoBoard.integration?.name ?? ""} の最新コミットを起点に、親ブランチと Draft のマージリクエストを作成します。`)) return false;
        const deps = await writeDeps(repoBoard.repo);
        const taken = [...repoBoard.candidates, ...repoBoard.families.map((f) => f.family.name)];
        const out = await ops.startIssue(repoBoard.repo, issue, repoBoard.seen ?? null, taken, deps);
        const done = out.kind === "started" || out.kind === "adopted";
        set({
          result: {
            kind: done ? "written" : out.kind,
            className: `notice ${done ? "ok" : "error"}`,
            text:
              out.kind === "started"
                ? `親ブランチ ${out.name} と Draft のマージリクエスト !${out.mr.number} を作成しました（${out.head.slice(0, 7)}）。親の提案の frontmatter に mr: ${out.mr.number} を必ず書き、エージェントにこのブランチで作業を開始するよう依頼してください。`
                : out.kind === "adopted"
                  ? `既存のマージリクエスト !${out.mr.number} の元ブランチ ${out.name}（識別子 ${out.identifier}）を採用しました。ブランチもマージリクエストも作っていません。親の提案の frontmatter に mr: ${out.mr.number} を必ず書き、エージェントにこのブランチで作業を開始するよう依頼してください。`
                  : `作業を開始できませんでした：${out.message}`,
          },
        });
        return true;
      });
    },
    setRecentDays(repoBoard, value) {
      void act(async () => {
        const key = repoKey(repoBoard.repo);
        try {
          const days = await ops.saveRecentDays(env.storage, env.hosts, key, value);
          set({ result: { kind: "written", className: "notice ok", text: `${key} の検索期間を ${days} 日に変更しました。再取得します。` } });
          return true;
        } catch (err) {
          set({ result: { kind: "refused", className: "notice error", text: `検索期間を保存できませんでした：${(err as Error).message ?? String(err)}` } });
          return false;
        }
      });
    },
    dismiss(repoBoard, family) {
      void (async () => {
        if (!env.confirm(`${family} の確認が必要な状態を解除します。ホストの履歴を確認し、親ブランチの置き場が正しいと確認できた場合のみ解除してください。`)) return;
        const all = await readAttention();
        const key = repoKey(repoBoard.repo);
        if (all[key]) delete all[key][family];
        await env.storage.set({ attention: all });
        set({ attention: all });
        drawRepo(snap.boards.get(key) ?? repoBoard, all);
      })();
    },
  };

  /**
   * 読み直す。失敗は投げずに `#status` へ出す（`state` は error。自動のときは文面だけ）。
   * 手動の読み直しは始めに欄を空にするので、失敗したときは、始める前に描いていた欄を戻す（読めた分は新しいまま）。
   */
  async function refresh(clearResult = true, fromTimer = false): Promise<void> {
    const before = snap.sections;
    try {
      await refreshOnce(clearResult, fromTimer);
    } catch (err) {
      const why = (err as Error).message ?? String(err);
      if (fromTimer) {
        set({ status: { ...snap.status, text: `自動更新に失敗しました：${why}` } });
      } else {
        set({ sections: new Map([...before, ...snap.sections]), status: { ...snap.status, text: `ボードを読み込めませんでした：${why}` }, state: "error" });
      }
    }
  }

  async function refreshOnce(clearResult: boolean, fromTimer: boolean): Promise<void> {
    while (inflight) {
      if (fromTimer) return;
      await inflight.catch(() => undefined);
    }
    const run = load(clearResult && !fromTimer, fromTimer);
    inflight = run;
    try {
      await run;
    } finally {
      inflight = null;
      auto?.done();
    }
  }

  async function load(clearResult: boolean, fromTimer: boolean): Promise<void> {
    const patch: { -readonly [K in keyof BoardSnapshot]?: BoardSnapshot[K] } = {};
    if (!fromTimer) patch.state = "loading";
    if (clearResult) patch.result = { ...snap.result, kind: undefined, text: "" };
    if (!fromTimer) patch.sections = new Map();
    if (Object.keys(patch).length > 0) set(patch);
    const got = await env.storage.get("repos");
    const repos = readRepos(got.repos, env.hosts);
    set({ repos });
    if (repos.length === 0) {
      set({ sections: new Map(), status: { ...snap.status, text: "リポジトリが登録されていません。設定画面で登録してください。" }, state: "done" });
      return;
    }
    if (!fromTimer) set({ status: { ...snap.status, text: "Python（Pyodide）を起動しています…" } });
    const w = pyWorker();
    const t = await w.init();
    set({ status: { text: `Pyodide ${t.pyodide}：起動 ${t.boot_ms} ms・読み込み ${t.import_ms} ms`, bootMs: t.boot_ms, importMs: t.import_ms, violations: t.violations.length } });
    const cache = await env.blobCache();
    const attention = await readAttention();
    set({ attention });
    for (const repo of repos) {
      const stats = newStats();
      const board = await ops.collectRepo(repo, { call: hostCall(repo.host, stats), py: w.call, cache, now: env.now, stats, workspace: workspaceOf(repo, stats) });
      set({ boards: new Map(snap.boards).set(repoKey(repo), board) });
      drawRepo(board, attention);
    }
    if (fromTimer) {
      // 外したリポジトリの欄を消す
      const keep = new Set(repos.map(repoKey));
      const sections = new Map([...snap.sections].filter(([key]) => keep.has(key)));
      if (sections.size !== snap.sections.size) set({ sections });
    }
    await drawBanner(repos);
    set({ state: "done" });
  }

  /** 自動の読み直しを飛ばす間: 読み直しが走っている、書く操作の最中、ボードの入力欄を触っている */
  function skipAuto(): boolean {
    return snap.busy || inflight !== null || env.editing();
  }

  return {
    snapshot: () => snap,
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    actions,
    refresh,
    openOptions: () => void env.openOptions(),
    start() {
      auto = startAutoRefresh({
        ...env.timers,
        busy: skipAuto,
        run: () => refresh(false, true),
      });
      void refresh();
    },
    reportRenderError(err) {
      set({ status: { ...snap.status, text: `ボードを表示できませんでした：${(err as Error)?.message ?? String(err)}` }, state: "error" });
    },
  };
}
