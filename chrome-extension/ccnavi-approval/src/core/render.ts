/**
 * ボードの DOM を組む。判定は出さず、Python の答えを並べるだけ（判定を 1 か所に保つため）。
 *
 * 素の文字列はすべて `textContent`。Markdown の本文だけを消毒した断片で入れる。
 * 承認と取り下げのボタンを出す。出すかは Python の答え（`write.allowed`・
 * `withdrawable` の理由）で決まり、ここは答えのとおりに並べるだけ。押したときの動き（`Actions`）は
 * 呼び手が渡す。渡さなければボタンを出さない（読み取りだけ）。
 * 依頼済みのフェーズのレビューの欄（MR のスレッドと、通らない理由）と「レビュー済みにする」を出す。
 * スレッドの本文は承認の画面と同じ規則で描く（Markdown は消毒した断片、隠れる書き方は通さない、HTML コメントは
 * 見える印。承認者に見えないまま承認させないため）。
 * GitLab の MR（`!番号`）とプロジェクトのリポジトリ、「始める」（issue の一覧と、押すと親のブランチを
 * 作るボタン）、打ち消しが収まらなかった家族の「要確認」を出す。issue の題も素の文字列（textContent）。
 */
import type { Renderer } from "./sanitize.js";
import type { ReviewPanel } from "./reviewed.js";
import { ALLOWED_URI } from "./sanitize.js";
import type { FamilyBoard, RepoBoard } from "./snapshot.js";
import type { Issue } from "./github.js";
import { repoKey } from "./settings.js";

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

/** ボタンを押したときの動き。呼び手（ボード）が渡す */
export interface Actions {
  approve(repo: RepoBoard, family: FamilyBoard): void;
  withdraw(repo: RepoBoard, family: FamilyBoard, ticket: string): void;
  /** レビュー済みにする。渡さなければ「レビュー済みにする」を出さない */
  review?(repo: RepoBoard, family: FamilyBoard, phase: number): void;
  /** issue の一覧を読む（「始める」）。渡さなければ「始める」の欄を出さない */
  loadIssues?(repo: RepoBoard): void;
  /** issue から親のブランチを作る */
  start?(repo: RepoBoard, issue: Issue): void;
  /** 「要確認」を外す（ユーザが確かめた） */
  dismiss?(repo: RepoBoard, family: string): void;
}

/** ボードの外から足すもの。家族ごとの「要確認」と、読んだ issue の一覧 */
export interface Extras {
  /** 家族の名前 → 打ち消しが収まらなかったときの文面 */
  readonly attention?: Readonly<Record<string, string>>;
  readonly issues?: { readonly list: readonly Issue[] | null; readonly error: string };
}

export function renderRepo(doc: Document, md: Renderer, board: RepoBoard, actions?: Actions, extras: Extras = {}): HTMLElement {
  const section = el(doc, "section", "repo");
  section.dataset.repo = repoKey(board.repo);
  const head = el(doc, "header", "repo-head");
  head.append(el(doc, "h2", "", `${board.repo.owner}/${board.repo.repo}`));
  if (board.repo.project) {
    const line = el(doc, "p", "project", `プロジェクト ${board.repo.project}（ワークスペース ${board.repo.workspace}。手元では projects/${board.repo.project}）`);
    line.dataset.testid = "project";
    head.append(line);
  }
  if (board.integration) {
    const source = board.integration.source === "setting" ? "設定" : "ホストのデフォルトブランチ";
    const line = el(doc, "p", "integration", `統合先: ${board.integration.name}（${source}） ${board.integration.head.slice(0, 7)}`);
    line.dataset.testid = "integration";
    head.append(line);
  }
  section.append(head);

  if (board.error) {
    section.append(notice(doc, "error", board.error));
    return section;
  }
  if (board.compat && !board.compat.same) {
    section.append(notice(doc, "warn", `${board.compat.message}（表示だけにして、承認と取り下げのボタンは出さない）`));
  }
  if (board.missingExtras.length > 0) {
    section.append(notice(doc, "warn", `指定したブランチがリモートに無い: ${board.missingExtras.join(", ")}`));
  }
  const scope = el(doc, "p", "scope", `見たブランチ（表示用）: ${board.candidates.length === 0 ? "なし" : board.candidates.join(", ")}`);
  section.append(scope);
  if (board.families.length === 0) {
    section.append(el(doc, "p", "empty", "見たブランチに親のブランチ（家族）は無い"));
  }
  for (const f of board.families) {
    const why = extras.attention?.[f.family.name];
    // 「要確認」の家族には、このブラウザでは書くボタンを出さない（ユーザが確かめて外すまで）
    const box = renderFamily(
      doc,
      md,
      f,
      why || !actions
        ? undefined
        : {
            approve: () => actions.approve(board, f),
            withdraw: (t) => actions.withdraw(board, f, t),
            review: actions.review ? (n: number) => actions.review?.(board, f, n) : undefined,
          },
    );
    if (why) box.insertBefore(attention(doc, why, actions?.dismiss ? () => actions.dismiss?.(board, f.family.name) : undefined), box.children[1] ?? null);
    section.append(box);
  }
  // 家族として見えていない（ブランチが見えなくなった）家族の「要確認」も出す
  for (const [name, why] of Object.entries(extras.attention ?? {})) {
    if (board.families.some((f) => f.family.name === name)) continue;
    const box = el(doc, "article", "family");
    box.dataset.family = name;
    box.append(el(doc, "h3", "", name));
    box.append(attention(doc, why, actions?.dismiss ? () => actions.dismiss?.(board, name) : undefined));
    section.append(box);
  }
  if (actions?.loadIssues && actions.start) section.append(renderStart(doc, board, actions, extras.issues));
  const s = board.stats;
  section.append(el(doc, "p", "stats", `読み取り: REST ${s.rest} 回・GraphQL ${s.graphql} 回・blob ${s.blobsFetched} 件（控えから ${s.blobsCached} 件）`));
  return section;
}

/** 打ち消しが収まらなかった家族。ユーザがホストの履歴を確かめたら外す */
function attention(doc: Document, text: string, onDismiss?: () => void): HTMLElement {
  const box = el(doc, "div", "attention");
  box.dataset.testid = "attention";
  box.append(notice(doc, "error", `要確認: ${text}`));
  box.append(
    el(doc, "p", "note", "この要確認はこのブラウザにだけ記録している（ほかの承認者には見えない）。ホストの履歴を確かめて直すまで、このブラウザからはこの家族に書かない"),
  );
  if (onDismiss) box.append(button(doc, "確かめた（要確認を外す）", "dismiss", onDismiss));
  return box;
}

/** 「始める」の欄。issue は押されてから読む（ボードを開くたびには読まない） */
function renderStart(doc: Document, board: RepoBoard, actions: Actions, issues?: Extras["issues"]): HTMLElement {
  const box = el(doc, "section", "start");
  box.dataset.testid = "start";
  box.append(el(doc, "h3", "", "issue から始める（親のブランチを統合先の先頭から作る。マージリクエストは最初の push の後に作られる）"));
  if (!issues) {
    box.append(button(doc, "issue を読む", "issues", () => actions.loadIssues?.(board)));
    return box;
  }
  if (issues.error) {
    box.append(notice(doc, "error", issues.error));
    return box;
  }
  const list = issues.list ?? [];
  if (list.length === 0) box.append(el(doc, "p", "empty", "開いている issue は無い"));
  const ul = el(doc, "ul", "issues");
  for (const issue of list) {
    const li = el(doc, "li", "issue");
    li.dataset.issue = String(issue.number);
    li.append(link(doc, issue.url, `#${issue.number}`), doc.createTextNode(` ${issue.title} `));
    li.append(button(doc, "始める", "start", () => actions.start?.(board, issue)));
    ul.append(li);
  }
  box.append(ul);
  return box;
}

function notice(doc: Document, kind: "error" | "warn", text: string): HTMLElement {
  const p = el(doc, "p", `notice ${kind}`, text);
  p.setAttribute("role", kind === "error" ? "alert" : "status");
  return p;
}

export interface FamilyActions {
  approve(): void;
  withdraw(ticket: string): void;
  review?(phase: number): void;
}

function button(doc: Document, text: string, action: string, onClick: () => void): HTMLButtonElement {
  const b = el(doc, "button", "action", text);
  b.type = "button";
  b.dataset.action = action;
  b.addEventListener("click", onClick);
  return b;
}

export function renderFamily(doc: Document, md: Renderer, f: FamilyBoard, actions?: FamilyActions): HTMLElement {
  const box = el(doc, "article", "family");
  box.dataset.family = f.family.name;
  const title = el(doc, "h3");
  title.append(el(doc, "code", "", f.family.name), doc.createTextNode(` ${f.family.title}`));
  box.append(title);
  if (f.error) {
    box.append(notice(doc, "error", f.error));
    return box;
  }
  const r = f.result;
  if (r === null) {
    return box;
  }
  const others = r.closure.families.filter((n) => n !== f.family.name);
  if (others.length > 0) {
    box.append(el(doc, "p", "closure", `判定に入れたほかの家族（先行をたどって行き着くもの）: ${others.join(", ")}`));
  }
  if (r.closure.absent.length > 0) {
    box.append(notice(doc, "warn", `先行の家族のブランチがリモートに無い: ${r.closure.absent.join(", ")}`));
  }
  if (r.undecided) {
    box.append(notice(doc, "error", r.undecided));
    return box;
  }
  if (r.refused) {
    box.append(notice(doc, "error", r.refused));
    return box;
  }
  const batch = r.batch ?? [];
  if (batch.length === 0) {
    box.append(el(doc, "p", "empty", "承認待ちは無い"));
  }
  if (r.text) {
    // ccnavi が出した承認の画面の本文（範囲・リスク・計画など）を、最初に開いた形で出す（REQ-APV-01 の
    // 「範囲を最初に」）。閉じた details には入れない。素の文字列なので textContent
    const screen = el(doc, "section", "screen");
    screen.dataset.testid = "screen";
    screen.append(el(doc, "h4", "", "承認の画面（ccnavi が出したもの。承認するとこのとおりに書く）"));
    screen.append(el(doc, "pre", "", r.text));
    box.append(screen);
  }
  for (const entry of batch) {
    const item = el(doc, "section", "entry");
    item.dataset.ticket = entry.ticket;
    const h = el(doc, "h4");
    h.append(el(doc, "code", "", entry.ticket), doc.createTextNode(` ${entry.title}`));
    if (entry.revision) h.append(el(doc, "span", "badge", "改版"));
    if (entry.phase !== null) h.append(el(doc, "span", "badge", `フェーズ ${entry.phase}`));
    item.append(h);
    item.append(el(doc, "p", "path", entry.path));
    for (const o of entry.overflow) item.append(notice(doc, "warn", o));
    const body = el(doc, "div", "markdown");
    body.append(md.markdown(entry.body));
    item.append(body);
    box.append(item);
  }
  const write = r.write ?? { allowed: false, reason: "" };
  if (batch.length > 0 && r.digest) {
    if (!write.allowed) {
      box.append(notice(doc, "warn", write.reason || "この家族には書けない"));
    } else if (actions) {
      const bar = el(doc, "div", "actions");
      bar.append(button(doc, `承認する（${batch.map((e) => e.ticket).join(", ")}）`, "approve", actions.approve));
      bar.append(el(doc, "span", "digest", `指紋 ${r.digest.slice(0, 12)}`));
      box.append(bar);
    }
  }
  const withdrawable = r.withdrawable ?? [];
  if (withdrawable.length > 0) {
    const list = el(doc, "section", "approved");
    list.append(el(doc, "h4", "", "作業中の承認済みチケット"));
    for (const w of withdrawable) {
      const item = el(doc, "div", "approved-item");
      item.dataset.ticket = w.ticket;
      item.append(el(doc, "code", "", w.ticket), doc.createTextNode(` ${w.title} `));
      if (w.problems.length === 0 && write.allowed && actions) {
        item.append(button(doc, "承認を取り下げる", "withdraw", () => actions.withdraw(w.ticket)));
      } else if (w.problems.length > 0) {
        const ul = el(doc, "ul", "why-not");
        for (const p of w.problems) ul.append(el(doc, "li", "", `取り下げられない: ${p}`));
        item.append(ul);
      }
      list.append(item);
    }
    box.append(list);
  }
  for (const panel of f.reviews ?? []) {
    box.append(renderReview(doc, md, panel, write.allowed, actions));
  }
  for (const rej of r.rejected ?? []) {
    const item = el(doc, "section", "rejected");
    item.dataset.ticket = rej.ticket;
    item.append(el(doc, "h4", "", `承認の対象にしない: ${rej.ticket}`));
    const ul = el(doc, "ul");
    for (const p of rej.problems) ul.append(el(doc, "li", "", p));
    item.append(ul);
    box.append(item);
  }
  if ((r.problems ?? []).length > 0) {
    const ul = el(doc, "ul", "problems");
    for (const p of r.problems ?? []) ul.append(el(doc, "li", "", p));
    box.append(ul);
  }
  return box;
}

/** リンクを http(s)・mailto の綴りのときだけ付ける。ほかは文字だけ */
function link(doc: Document, href: string, text: string): HTMLElement {
  if (!ALLOWED_URI.test(href.trim())) return el(doc, "span", "", text);
  const a = el(doc, "a", "", text);
  a.setAttribute("href", href);
  a.setAttribute("target", "_blank");
  a.setAttribute("rel", "noopener noreferrer");
  return a;
}

/**
 * 依頼済みのフェーズのレビューの欄。スレッドは未解決を先に、本文は承認の画面と同じ消毒で描く。
 * 「レビュー済みにする」は、Python が通さない理由を返さず、書ける家族で、読めたときだけ出す。
 */
export function renderReview(doc: Document, md: Renderer, panel: ReviewPanel, writable: boolean, actions?: FamilyActions): HTMLElement {
  const box = el(doc, "section", "review");
  box.dataset.phase = String(panel.phase);
  const h = el(doc, "h4");
  h.append(doc.createTextNode(`フェーズ ${panel.phase} のレビュー（`));
  // GitLab の MR は `!番号`、GitHub の PR は `#番号`
  const mark = (panel.copy?.host ?? panel.host) === "gitlab" ? "!" : "#";
  h.append(panel.copy ? link(doc, panel.copy.mr.url, `マージリクエスト ${mark}${panel.copy.mr.number}`) : doc.createTextNode(`マージリクエスト ${mark}${panel.mr}`));
  h.append(doc.createTextNode(`）: ${panel.children.join(", ")}`));
  box.append(h);
  if (panel.error) {
    box.append(notice(doc, "error", panel.error));
    return box;
  }
  // 並べるのは写しのとおり。GitHub では目印で始まるスレッドもユーザのものとして数え、GitLab で依頼を投稿したアカウントの
  // ccnavi の依頼のスレッドを数えないのは Python。ここは未解決の件数を写しのとおりに出す
  const threads = [...(panel.copy?.threads ?? [])].sort((a, b) => Number(a.resolved) - Number(b.resolved));
  const unresolved = threads.filter((t) => !t.resolved).length;
  box.append(el(doc, "p", "threads-count", `スレッド ${threads.length} 件（未解決 ${unresolved} 件）・レビュー ${panel.copy?.reviews.length ?? 0} 件`));
  const list = el(doc, "ul", "threads");
  for (const t of threads) {
    const item = el(doc, "li", "thread");
    item.dataset.resolved = String(t.resolved);
    const head = el(doc, "p", "thread-head");
    head.append(el(doc, "span", "badge", t.resolved ? "解決済み" : "未解決"));
    head.append(doc.createTextNode(" "));
    head.append(link(doc, t.url, t.path ? `${t.path}:${t.line}` : "（行なし）"));
    item.append(head);
    const body = el(doc, "div", "markdown");
    body.append(md.markdown(t.body));
    item.append(body);
    list.append(item);
  }
  box.append(list);
  if (panel.problems.length > 0) {
    const ul = el(doc, "ul", "problems");
    for (const p of panel.problems) ul.append(el(doc, "li", "", p));
    box.append(ul);
  } else if (writable && actions?.review) {
    const review = actions.review;
    const bar = el(doc, "div", "actions");
    bar.append(button(doc, `フェーズ ${panel.phase} をレビュー済みにする`, "review", () => review(panel.phase)));
    box.append(bar);
  }
  return box;
}
