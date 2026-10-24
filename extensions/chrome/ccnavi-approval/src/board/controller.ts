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
import { DECIDE_CHOICES, DECIDE_LABELS, type DecideChoice, type DecidePreview } from "../core/reviewed.js";
import { approveFamily, confirmPhase, decidePhase, decidePreview, type DecideOutcome, type Outcome, type WriteDeps } from "../core/write.js";
import type { PyWorker } from "./py-client.js";

/** 親子のチケットごとの「要確認」（リポジトリのキー → 親のブランチ名 → 文面）。`chrome.storage.local` の `attention` */
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
  /** 残った指摘の行き先を決める画面を開く（プレビューを読む）。渡さなければ画面を出さない */
  decideOpen?(repo: RepoBoard, family: FamilyBoard, phase: number): void;
  /** 選んだ行き先（キー → 行き先）と、見せたダイジェストで確定する */
  decideConfirm?(repo: RepoBoard, family: FamilyBoard, phase: number, choices: Readonly<Record<string, DecideChoice>>, digest: string): void;
  /** 決める画面を閉じる */
  decideCancel?(): void;
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

/** 決める画面の状態。`preview` は読み込み中は null。読み込みの失敗は `notice` に出す */
export interface DecideState {
  /** リポジトリのキー（`repoKey`）。同じ名前の親が別のリポジトリにあっても取り違えない */
  readonly repo: string;
  /** 開くたびに増える番号。遅れて届いた読み取りの結果が、開き直した画面に付かないようにする */
  readonly token: number;
  readonly family: string;
  readonly phase: number;
  readonly preview: DecidePreview | null;
  /** 確定の最中（画面は全部押せなくする） */
  readonly deciding: boolean;
  /** 画面に出す一言（見せた指摘が変わった・読めなかった・断られた理由） */
  readonly notice: string;
  /** 見せ直したとき、キーと版が同じ指摘から引き継いだ選択（画面は新しいダイジェストの初期値にする） */
  readonly inherited?: Readonly<Record<string, DecideChoice>>;
}

export type { DecideDone, DecideOutcome } from "../core/write.js";

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
  /** 残った指摘の行き先を決める画面（開いていなければ無い） */
  readonly decide?: DecideState;
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
  /** 決める画面の材料（残った指摘とダイジェスト）を読む。書かない */
  readonly decidePreview: (repo: RepoConfig, family: string, phase: number, deps: WriteDeps) => Promise<DecidePreview>;
  /**
   * 選んだ行き先を、issue・返信・解決の順にホストへ書いてから親のブランチへ 1 コミットする。
   * `choices` が空（未解決の指摘が 0 件）のときは、従来の「レビュー済みにする」と同じ結果（`reviewed: true`）にする。
   */
  readonly decide: (
    repo: RepoConfig,
    family: string,
    phase: number,
    choices: Readonly<Record<string, DecideChoice>>,
    digest: string,
    deps: WriteDeps,
  ) => Promise<DecideOutcome>;
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

const DEFAULT_OPS: BoardOps = {
  collectRepo,
  approveFamily,
  confirmPhase,
  decidePreview,
  decide: decidePhase,
  listIssues,
  startIssue,
  saveRecentDays,
};

/** 決めた結果の一言。途中で止まった報告は、済んだ分と理由、続きの案内を出す */
export function sayDecide(out: DecideOutcome): ResultLine {
  if (out.kind === "written") {
    const extra = out.pendingExtra ?? 0;
    const head = out.reviewed ? "レビュー済みにしました" : extra > 0 && !out.followup ? `行き先は反映しましたが、見せた後に増えた指摘 ${extra} 件が判断待ちで、フェーズはレビュー済みにしていません` : "行き先を反映しました";
    const parts = [head];
    if (out.followup) parts.push(`続きの子：${out.followup}`);
    if (extra > 0 && out.followup) parts.push(`見せた後に増えた指摘 ${extra} 件は判断待ちで、フェーズはレビュー済みにしていません`);
    if (out.issues.length > 0) parts.push(`作った issue：${out.issues.join(", ")}`);
    const reused = out.reusedIssues ?? [];
    if (reused.length > 0) parts.push(`既にあった issue ${reused.length} 件：${reused.join(", ")}`);
    const commit = out.oid ? `（コミット：${out.oid.slice(0, 7)}）` : "（親のブランチへ積むものはありませんでした）";
    const warn = out.warning ? ` 注意：${out.warning}。` : "";
    return { kind: "written", className: warn ? "notice error" : "notice ok", text: `${parts.join("。")}${commit}。${warn}` };
  }
  if (out.kind === "halted") {
    const d = out.done;
    const made = d.issues.length > 0 ? `作った issue：${d.issues.join(", ")}` : "作った issue：なし";
    const reused = (d.reusedIssues ?? []).length > 0 ? `。既にあった issue ${d.reusedIssues?.length} 件：${d.reusedIssues?.join(", ")}` : "";
    return {
      kind: "halted",
      className: "notice error",
      text: `ホストへの書き込みが途中で止まりました。済んだ分（${made}${reused}。返信 ${d.replied} 件・解決 ${d.resolved} 件）。止まった理由：${out.message}。ボードを読み直して、未解決の指摘だけ決め直してください。`,
    };
  }
  if (out.kind === "mismatch") return { kind: "changed", className: "notice error", text: `指摘が変わっていたので反映していません：${out.message}` };
  return sayOutcome(out, "指摘の行き先");
}

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

/** 従来の「レビュー済みにする」の確認文（指摘が 0 件の決めるも同じ文面） */
function reviewMessage(family: string, phase: number, warn: string): string {
  return `${family} のフェーズ ${phase} をレビュー済みにする。レビュー待ちの子チケットを done/ に移動し、レビュー済みマーカーとともに1つのコミットとして反映します。反映する前に、押した時点のスレッドとレビューを再取得して確認します。${warn}`;
}

function decideMessage(family: string, phase: number, choices: Readonly<Record<string, DecideChoice>>, warn: string): string {
  const n = (c: DecideChoice): number => Object.values(choices).filter((v) => v === c).length;
  const parts = `${DECIDE_LABELS.fix} ${n("fix")} 件・${DECIDE_LABELS.issue} ${n("issue")} 件・${DECIDE_LABELS.keep} ${n("keep")} 件`;
  const fix = n("fix") > 0 ? `「${DECIDE_LABELS.fix}」が 1 件以上あるので、続きの子チケットを作り、このフェーズを再開します。` : "";
  return `${family} のフェーズ ${phase} の残った指摘の行き先を反映します（${parts}）。${fix}issue の作成、スレッドへの返信、スレッドの解決の順にホストへ書いてから、親のブランチへ1つのコミットとして反映します。${warn}`;
}

/** 読み取った指摘のキーが空か重複していれば理由を返す（行き先を結ぶキーとして使えない） */
function previewProblem(preview: DecidePreview): string {
  const seen = new Set<string>();
  for (const t of preview.threads) {
    if (typeof t.key !== "string" || t.key === "") return "指摘のキーが空です";
    if (seen.has(t.key)) return `指摘のキーが重複しています（${t.key}）`;
    seen.add(t.key);
  }
  return "";
}

/** 選んだ行き先が、見せた指摘の全部に 1 つずつ付いているか。付いていなければ理由を返す */
function choicesProblem(preview: DecidePreview, choices: Readonly<Record<string, DecideChoice>>, digest: string): string {
  if (digest !== preview.digest) return "見せた指摘と確定の内容が食い違っています。閉じて開き直してください";
  if (preview.threads.some((t) => t.unsupported)) return "Chrome では扱えない指摘があります。VS Code で決めてください";
  const keys = preview.threads.map((t) => t.key);
  const given = Object.keys(choices);
  if (given.length !== keys.length || !keys.every((k) => k in choices)) return "行き先を選んでいない指摘か、見せていない指摘があります";
  if (!given.every((k) => DECIDE_CHOICES.includes(choices[k]))) return "不明な行き先があります";
  return "";
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

  function setDecide(patch: Partial<DecideState> | undefined): void {
    if (patch === undefined) {
      set({ decide: undefined });
      return;
    }
    if (snap.decide) set({ decide: { ...snap.decide, ...patch } });
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
        const msg = reviewMessage(family.family.name, phase, warn);
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
          got = { list: null, error: `Issue 一覧を取得できませんでした：${(err as Error).message ?? String(err)}` };
        }
        set({ issues: new Map(snap.issues).set(key, got) });
        drawRepo(snap.boards.get(key) ?? repoBoard, await readAttention());
      })();
    },
    start(repoBoard, issue) {
      void act(async () => {
        if (!env.confirm(`Issue #${issue.number} から作業を始めます。issue を閉じる開いたマージリクエストがあれば、その元ブランチを採用します（ブランチもマージリクエストも作りません）。無ければ、統合先 ${repoBoard.integration?.name ?? ""} の最新コミットを起点に、親ブランチと Draft のマージリクエストを作成します。`)) return false;
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
                ? `親ブランチ ${out.name} と Draft のマージリクエスト !${out.mr.number} を作成しました（${out.head.slice(0, 7)}）。親の提案の frontmatter に issue: ${issue.number} と mr: ${out.mr.number} を必ず書き、エージェントにこのブランチで作業を開始するよう依頼してください。`
                : out.kind === "adopted"
                  ? `既存のマージリクエスト !${out.mr.number} の元ブランチ ${out.name}（識別子 ${out.identifier}）を採用しました。ブランチもマージリクエストも作っていません。親の提案の frontmatter に issue: ${issue.number} と mr: ${out.mr.number} を必ず書き、エージェントにこのブランチで作業を開始するよう依頼してください。`
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

  /** 決める画面の入口 */
  const { decidePreview, decide: decideWrite } = ops;
  let decideSeq = 0;
  const live = (token: number): boolean => snap.decide?.token === token;
  const patchOf = (token: number, patch: Partial<DecideState>): void => {
    if (live(token)) setDecide(patch);
  };
  actions.decideOpen = (repoBoard, family, phase) => {
    if (snap.busy) return;
    const name = family.family.name;
    const token = ++decideSeq;
    set({ decide: { repo: repoKey(repoBoard.repo), token, family: name, phase, preview: null, deciding: false, notice: "" } });
    void (async () => {
      try {
        if (await blocked(repoBoard.repo, name)) {
          if (live(token)) setDecide(undefined);
          return;
        }
        const preview = await decidePreview(repoBoard.repo, name, phase, await writeDeps(repoBoard.repo));
        const bad = previewProblem(preview);
        if (bad) patchOf(token, { notice: `残った指摘を読み取れませんでした：${bad}` });
        else patchOf(token, { preview });
      } catch (err) {
        patchOf(token, { notice: `残った指摘を読み取れませんでした：${(err as Error).message ?? String(err)}` });
      }
    })();
  };
  actions.decideConfirm = (repoBoard, family, phase, choices, digest) => {
    void act(async () => {
      const name = family.family.name;
      const cur = snap.decide;
      if (!cur || cur.repo !== repoKey(repoBoard.repo) || cur.family !== name || cur.phase !== phase || !cur.preview) return false;
      const token = cur.token;
      const preview = cur.preview;
      const bad = choicesProblem(preview, choices, digest);
      if (bad) {
        setDecide({ notice: bad });
        return false;
      }
      if (await blocked(repoBoard.repo, name)) return false;
      const deps = await writeDeps(repoBoard.repo);
      const warn = await approvalWarning(deps, repoBoard.repo, name);
      const msg = Object.keys(choices).length === 0 ? reviewMessage(name, phase, warn) : decideMessage(name, phase, choices, warn);
      if (!env.confirm(msg)) return false;
      setDecide({ deciding: true, notice: "" });
      let out: DecideOutcome;
      try {
        out = await decideWrite(repoBoard.repo, name, phase, choices, digest, deps);
      } catch (err) {
        out = { kind: "failed", message: (err as Error).message ?? String(err) };
      }
      if (out.kind === "attention") await noteAttention(repoBoard.repo, name, out);
      if (out.kind === "mismatch" && !out.hostWritten) {
        // 見せた指摘が変わった。読み直して選び直させる（キーと版が同じ指摘の選択は引き継ぐ）
        try {
          const next = await decidePreview(repoBoard.repo, name, phase, deps);
          const problem = previewProblem(next);
          if (problem) {
            patchOf(token, { preview: null, deciding: false, notice: `指摘が変わりました。読み直しましたが読み取れませんでした：${problem}` });
          } else {
            const inherited: Record<string, DecideChoice> = {};
            for (const t of next.threads) {
              const before = preview.threads.find((o) => o.key === t.key && o.version === t.version);
              if (before && choices[t.key]) inherited[t.key] = choices[t.key];
            }
            patchOf(token, { preview: next, deciding: false, inherited, notice: `指摘が変わりました（${out.message}）。同じ指摘の選択は引き継ぎましたが、新しい指摘は「${DECIDE_LABELS.fix}」に戻っています。選び直してください。` });
          }
        } catch (err) {
          patchOf(token, { deciding: false, notice: `指摘が変わりました。読み直せませんでした：${(err as Error).message ?? String(err)}` });
        }
        return false;
      }
      set({ result: sayDecide(out) });
      if (out.kind === "written" || out.kind === "halted" || out.kind === "attention" || ("hostWritten" in out && out.hostWritten)) setDecide(undefined);
      else patchOf(token, { deciding: false, notice: sayDecide(out).text });
      return true;
    }).then(() => {
      if (snap.decide?.deciding) setDecide({ deciding: false });
    });
  };
  actions.decideCancel = () => {
    if (snap.busy) return;
    setDecide(undefined);
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
    // 書いた後の読み直しでは決める画面を残す（失敗の理由を見せ続ける）。手で押した読み直しだけ閉じる
    if (clearResult && !fromTimer) patch.decide = undefined;
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
    return snap.busy || inflight !== null || env.editing() || snap.decide !== undefined;
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
