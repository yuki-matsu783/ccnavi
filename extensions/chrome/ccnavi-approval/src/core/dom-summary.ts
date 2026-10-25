/**
 * 描いたボードの DOM の要約（開発用。`scripts/render-scene.js` が出す `board.dom.json`）。
 *
 * 親子のチケットの欄（`article.family[data-family]`）ごとに、承認待ちの札（`section.entry[data-ticket]`）、
 * レビュー待ちの欄（`section.review[data-phase]`）、承認対象外（`section.rejected`）、利用できる操作を取り出す。
 * 取るのは画面にある属性とクラスだけで、承認待ちかレビュー待ちかは `data-waiting`（欄の種類）から読む（判定はここでしない）。
 * 承認などの操作ができるかは `actions`（押せるボタン）で見る。`waiting` は操作できることを表さない。
 *
 * 実行先のページへ `fn.toString()` で渡して動かすので、この関数は import も外の値も使わず、それだけで完結させる。
 * 返す形（`schema: 1`）は VS Code 側の同じ要約と揃える（Chrome の `family` は VS Code 側では `parent`）。
 */

export interface DomEntry {
  readonly ticket: string;
  readonly title: string;
  readonly badges: string[];
  readonly waiting: string;
  readonly actions: string[];
}

export interface DomReview {
  readonly phase: string;
  readonly title: string;
  readonly waiting: string;
  readonly threads: { readonly open: number; readonly resolved: number };
  readonly notices: string[];
  readonly actions: string[];
}

export interface DomFamily {
  readonly repo: string;
  readonly name: string;
  readonly title: string;
  readonly notices: string[];
  readonly entries: DomEntry[];
  readonly reviews: DomReview[];
  readonly rejected: { readonly ticket: string; readonly reason: string }[];
  readonly actions: string[];
}

export interface BoardSummary {
  readonly schema: 1;
  readonly state: string;
  readonly status: string;
  readonly families: DomFamily[];
  /** 欄ごとの読み込みの失敗（`section.repo` の通知）。家族の欄に属さないもの */
  readonly repoNotices: { readonly repo: string; readonly text: string }[];
}

export function summarizeBoard(doc: Document): BoardSummary {
  const text = (el: Element | null): string => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
  // 押せる操作のラベル（disabled は含めない）。`skip` に入るボタン（レビュー待ちの欄の中）は数えない
  const actionsOf = (root: Element, skip: string): string[] =>
    Array.from(root.querySelectorAll("button"))
      .filter((b) => !(b as HTMLButtonElement).disabled && (skip === "" || b.closest(skip) === null))
      .map((b) => text(b))
      .filter((label) => label !== "");
  // 見出しから `code`（識別子）と札を除いた題
  const titleOf = (h: Element | null): string => {
    if (h === null) return "";
    const copy = h.cloneNode(true) as Element;
    for (const x of Array.from(copy.querySelectorAll("code, .badge"))) x.remove();
    return text(copy).replace(/（識別子\s*）/g, "").trim();
  };
  const families: DomFamily[] = [];
  const repoNotices: { repo: string; text: string }[] = [];
  for (const repoEl of Array.from(doc.querySelectorAll("section.repo"))) {
    const repo = repoEl.getAttribute("data-repo") ?? "";
    for (const note of Array.from(repoEl.querySelectorAll(":scope > .notice"))) repoNotices.push({ repo, text: text(note) });
    for (const fam of Array.from(repoEl.querySelectorAll("article.family[data-family]"))) {
      families.push({
        repo,
        name: fam.getAttribute("data-family") ?? "",
        title: titleOf(fam.querySelector(":scope > h3")),
        notices: Array.from(fam.querySelectorAll(":scope > .notice, :scope > .attention .notice")).map((n) => text(n)),
        entries: Array.from(fam.querySelectorAll(":scope > section.entry[data-ticket]")).map((e) => ({
          ticket: e.getAttribute("data-ticket") ?? "",
          title: titleOf(e.querySelector("h4")),
          badges: Array.from(e.querySelectorAll("h4 .badge")).map((b) => text(b)),
          waiting: e.getAttribute("data-waiting") ?? "",
          actions: actionsOf(e, ""),
        })),
        reviews: Array.from(fam.querySelectorAll(":scope > section.review[data-phase]")).map((r) => ({
          phase: r.getAttribute("data-phase") ?? "",
          title: text(r.querySelector("h4")),
          waiting: r.getAttribute("data-waiting") ?? "",
          threads: {
            open: r.querySelectorAll('li.thread[data-resolved="false"]').length,
            resolved: r.querySelectorAll('li.thread[data-resolved="true"]').length,
          },
          notices: Array.from(r.querySelectorAll(":scope > .notice, :scope > .problems li")).map((n) => text(n)),
          actions: actionsOf(r, ""),
        })),
        rejected: Array.from(fam.querySelectorAll(":scope > section.rejected")).map((r) => ({
          ticket: r.getAttribute("data-ticket") ?? "",
          reason: Array.from(r.querySelectorAll("li")).map((li) => text(li)).join(" / "),
        })),
        // 欄の直下の操作（承認）。レビュー待ちの欄の中のボタンは `reviews[].actions` に入る
        actions: actionsOf(fam, "section.review"),
      });
    }
  }
  return {
    schema: 1,
    state: doc.body.getAttribute("data-state") ?? "",
    status: text(doc.getElementById("status")),
    families,
    repoNotices,
  };
}
