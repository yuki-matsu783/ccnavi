/**
 * 見本のリポジトリ。模擬の GitHub（test/helpers/mock-github.ts）がこれを API で返す。
 *
 * - `main`（統合先）: 共通層、互換のマーカー、閉じた親子のチケット i0005 の `done/`
 * - `i0001`（直近）: 親と子の提案。子の先行は i0003-01-01（開いた親子のチケット。直近の外なので閉包で読む）と
 *   i0005-01-01（統合先で閉じている。そこで止まる）
 * - `i0002`（直近）: 悪意のある Markdown を本文に持つ親の提案と、範囲が親の外に出る子（承認の対象にしない）。
 *   子の先行 i0007-01-01 の親子のチケットのブランチは無い
 * - `i0003`（古い）: 親と子の提案。表示用のブランチには入らない
 * - `feature-x`（直近）: コードだけのブランチ（親子のチケットのブランチではない）
 */

import { COMPAT } from "../helpers/compat.js";

export const NOW = "2026-09-29T00:00:00Z";

const PHASES = `version: 1
phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*"]
`;

const RULES = `{"version": 1, "deny": []}
`;

const COMMON_SH = (compat: number) => `#!/bin/sh
# 見本の共通部。互換の版だけを持つ。
CCNAVI_COMPAT=${compat}
`;

function parent(id: string, title: string, body: string, plan = ["research", "design"]): string {
  return [
    "---",
    "version: 1",
    `ticket: ${id}`,
    "plan:",
    ...plan.map((p) => `  - ${p}`),
    "human_review:",
    "  required: true",
    "  reason: 見本",
    `title: ${title}`,
    "rationale: 見本の理由",
    "allow:",
    "  - match: Write|Edit",
    '    glob: "src/*"',
    "  - match: Write|Edit",
    '    glob: "wip/*"',
    'started_at: ""',
    'completed_at: ""',
    'base_sha: ""',
    "---",
    "",
    body,
  ].join("\n");
}

function child(id: string, parentId: string, phase: number, glob: string, predecessors: string[] = [], extra: string[] = []): string {
  return [
    "---",
    "version: 1",
    `ticket: ${id}`,
    `parent: ${parentId}`,
    `phase: ${phase}`,
    ...(predecessors.length ? ["predecessors:", ...predecessors.map((p) => `  - ${p}`)] : []),
    "human_review:",
    "  required: false",
    "  reason: 見本",
    `title: 子 ${id}`,
    "rationale: 見本の理由",
    "allow:",
    "  - match: Write|Edit",
    `    glob: "${glob}"`,
    'started_at: ""',
    'completed_at: ""',
    'base_sha: ""',
    ...extra,
    "---",
    "",
    `子 ${id} の本文`,
    "",
  ].join("\n");
}

function done(id: string, parentId: string | null, phase: number | null): string {
  return [
    "---",
    "version: 1",
    `ticket: ${id}`,
    ...(parentId ? [`parent: ${parentId}`, `phase: ${phase}`] : ["plan:", "  - research"]),
    `title: 閉じた ${id}`,
    "human_review:",
    "  required: false",
    "  reason: 見本",
    "rationale: 見本の理由",
    "allow:",
    "  - match: Write|Edit",
    `    glob: "${parentId ? "wip/research/*" : "wip/*"}"`,
    "started_at: 2026-09-01T10:00:00+0900",
    "completed_at: 2026-09-02T10:00:00+0900",
    "base_sha: 0000000000000000000000000000000000000000",
    "ccnavi_approved:",
    "  approved_at: 2026-09-01T09:00:00+0900",
    `  source_tree: ${parentId ?? id}`,
    "  source_path: wip/proposals/todo/" + id + ".md",
    "---",
    "",
    "閉じた",
    "",
  ].join("\n");
}

/** 悪意のある Markdown。描いても何も動かないことを試験が見る */
export const HOSTILE_MARKDOWN = [
  "# 悪意のある本文",
  "",
  "<script>window.__pwned = 'script'</script>",
  "",
  '<img src="x" onerror="window.__pwned = \'img\'">',
  "",
  "[クリック](javascript:window.__pwned='link')",
  "",
  '<a href="javascript:window.__pwned=\'raw-a\'">生の a</a>',
  "",
  '<svg onload="window.__pwned=\'svg\'"><circle r="4"/></svg>',
  "",
  "[データ](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "",
  '<a href="jav&#x61;script:window.__pwned=\'entity\'">実体参照で崩したスキーム</a>',
  "",
  '<a href="  JaVaScRiPt:window.__pwned=\'case\'">大文字と空白</a>',
  "",
  '<iframe src="https://example.com"></iframe>',
  "",
  '<form action="https://example.com"><input name="x"><button>送る</button></form>',
  "",
  '<p style="background:url(https://example.com/x)" onclick="window.__pwned=\'click\'">style と onclick</p>',
  "",
  "[普通のリンク](https://example.com/ok) と <mailto:a@example.com>",
  "",
  "![画像](https://example.com/track.png)",
  "",
].join("\n");

export interface FixtureBranch {
  readonly committedDate: string;
  readonly files: Record<string, string>;
}

const MAIN_FILES: Record<string, string> = {
  ".claude/settings.json": JSON.stringify({ env: { CCNAVI_TICKET_CONTROL: "enable" } }, null, 2) + "\n",
  ".ccnavi/scripts/ccnavi-common.sh": COMMON_SH(1),
  ".ccnavi/common/phases.yml": PHASES,
  ".ccnavi/common/rules.yml": RULES,
  ".ccnavi/approved/done/i0005.md": done("i0005", null, null),
  ".ccnavi/approved/done/i0005-01-01.md": done("i0005-01-01", "i0005", 1),
  "README.md": "見本\n",
  "src/app.py": "print('main')\n",
};

export function fixture(compat = COMPAT): Record<string, FixtureBranch> {
  const main = { ...MAIN_FILES, ".ccnavi/scripts/ccnavi-common.sh": COMMON_SH(compat) };
  return {
    main: { committedDate: "2026-09-20T00:00:00Z", files: main },
    i0001: {
      committedDate: "2026-09-28T10:00:00Z",
      files: {
        ...main,
        "wip/proposals/todo/i0001.md": parent("i0001", "見本の親 i0001", "## やること\n\n- **調べる**\n- `設計` する\n"),
        "wip/proposals/todo/i0001-01-01.md": child("i0001-01-01", "i0001", 1, "wip/research/*", ["i0003-01-01", "i0005-01-01"]),
        "src/app.py": "print('i0001')\n",
      },
    },
    i0002: {
      committedDate: "2026-09-28T12:00:00Z",
      files: {
        ...main,
        "wip/proposals/todo/i0002.md": parent("i0002", "悪意のある本文を持つ親 i0002", HOSTILE_MARKDOWN, ["research"]),
        "wip/proposals/todo/i0002-01-01.md": child("i0002-01-01", "i0002", 1, "docs/*", ["i0007-01-01"]),
      },
    },
    i0003: {
      committedDate: "2026-09-01T00:00:00Z",
      files: {
        ...main,
        "wip/proposals/todo/i0003.md": parent("i0003", "古い親子のチケット i0003", "古い本文\n", ["research"]),
        "wip/proposals/todo/i0003-01-01.md": child("i0003-01-01", "i0003", 1, "wip/research/*"),
      },
    },
    "feature-x": {
      committedDate: "2026-09-28T08:00:00Z",
      files: { ...main, "src/app.py": "print('x')\n" },
    },
  };
}

/** 承認済みの写し（作業中の親、レビュー待ちの子）。レビュー済みの見本 */
function approvedCopy(id: string, parentId: string | null, lines: string[], phases = 1, phase = 1): string {
  return [
    "---",
    "version: 1",
    `ticket: ${id}`,
    ...(parentId ? [`parent: ${parentId}`, `phase: ${phase}`] : ["plan:", ...Array.from({ length: phases }, () => "  - design")]),
    `title: ${parentId ? `設計の子 ${id}` : `レビューを待つ親 ${id}`}`,
    "human_review:",
    "  required: true",
    "  reason: 見本",
    "rationale: 見本の理由",
    "allow:",
    "  - match: Write|Edit",
    `    glob: "${parentId ? "wip/design/*" : "wip/*"}"`,
    "started_at: 2026-09-27T10:00:00+0900",
    ...lines,
    "base_sha: 0000000000000000000000000000000000000000",
    "ccnavi_approved:",
    "  approved_at: 2026-09-27T09:00:00+0900",
    `  source_tree: ${parentId ?? id}`,
    `  source_path: wip/proposals/todo/${id}.md`,
    "---",
    "",
    `${id} の本文`,
    "",
  ].join("\n");
}

/**
 * レビューを依頼したフェーズを持つ親子のチケット。親は作業中（計画は MR で見る design の 1 フェーズ）、
 * 子はレビュー待ち。依頼のマーカーは `requested`（`head` は依頼時の先頭。見本を積んでから書く）
 */
export function reviewFamilyFiles(id: string, phases = 1): Record<string, string> {
  const out: Record<string, string> = {
    [`.ccnavi/approved/doing/${id}.md`]: approvedCopy(id, null, ['completed_at: ""'], phases),
    "wip/design/plan.md": "設計\n",
  };
  for (let n = 1; n <= phases; n += 1) {
    const child = `${id}-0${n}-01`;
    out[`wip/proposals/review/${child}.md`] = approvedCopy(child, id, ["completed_at: 2026-09-28T10:00:00+0900"], 1, n);
  }
  return out;
}

/** 依頼のマーカー（`ccnavi review requested` が置く形）。GitLab は依頼を投稿したアカウント（`poster`）も持つ */
export function requestedMark(head: string, mr = 42, host: "github" | "gitlab" = "github", poster = ""): string {
  const url = host === "gitlab" ? `https://gitlab.com/acme/widgets/-/merge_requests/${mr}#note_1` : `https://github.com/acme/widgets/pull/${mr}#issuecomment-1`;
  return JSON.stringify({ head, mr, url, host, since: "2026-09-29T00:00:00Z", ...(poster ? { poster } : {}) });
}

/**
 * プロジェクトのリポジトリ（手元では `projects/web` に clone されるもの）。模擬の GitLab に載せる。
 *
 * - `main`（プロジェクトの統合先）: 閉じた親子のチケット web-i0003 の `done/` とプロジェクトの層（rules.yml だけ）。
 *   共通層・置き場の綴り・互換のマーカーはワークスペース（`fixture()` の `main`）から読む
 * - `web-i0012`（直近）: issue #12 から始めた親と子の提案
 * - `web-i0012` の上の `.ccnavi/config/phases.yml` は読まない（置き場の外）
 */
export function projectFixture(): Record<string, FixtureBranch> {
  const main = {
    ".ccnavi/config/rules.yml": RULES,
    ".ccnavi/approved/done/web-i0003.md": done("web-i0003", null, null),
    "README.md": "プロジェクト\n",
  };
  return {
    main: { committedDate: "2026-09-20T00:00:00Z", files: main },
    "web-i0012": {
      committedDate: "2026-09-28T10:00:00Z",
      files: {
        ...main,
        ".ccnavi/config/phases.yml": "version: 1\nphases: {}\n",
        "wip/proposals/todo/web-i0012.md": parent("web-i0012", "プロジェクトの親 web-i0012", "プロジェクトの本文\n"),
        "wip/proposals/todo/web-i0012-01-01.md": child("web-i0012-01-01", "web-i0012", 1, "wip/research/*"),
      },
    },
  };
}
