/**
 * `.claude/settings.json` と `.claude/settings.local.json` の hooks を読み、
 * 「このツール名でどの hook が走るか」を答える。
 *
 * 読むだけで書かない。利用者ごとの設定（~/.claude/settings.json）は見ない。
 * ワークスペースの外を読む道具にしないため。載らないことは画面に書く。
 *
 * matcher の意味は Claude Code のもので、ccnavi の判定ではない。空か `*` なら全部、
 * それ以外はツール名に対する正規表現（`Write|Edit` のように書ける）。正規表現として
 * 読めなければ文字列そのものと比べる。ここは ccnavi の答えを出し直す場所ではなく、
 * 配線を見せる場所。
 */

export type HookSource = "settings" | "settings-local";

export interface HookEntry {
  readonly source: HookSource;
  readonly event: string;
  readonly matcher: string;
  readonly command: string;
  readonly timeout: number | null;
}

/** ツール名を持つイベント。これ以外は matcher に関係なく常に走る */
export const TOOL_EVENTS = ["PreToolUse", "PostToolUse"] as const;

export function parseHooks(text: string, source: HookSource): HookEntry[] {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return [];
  }
  if (!isRecord(raw) || !isRecord(raw.hooks)) {
    return [];
  }
  const entries: HookEntry[] = [];
  for (const [event, groups] of Object.entries(raw.hooks)) {
    if (!Array.isArray(groups)) {
      continue;
    }
    for (const group of groups) {
      if (!isRecord(group)) {
        continue;
      }
      const matcher = typeof group.matcher === "string" ? group.matcher : "";
      const hooks = Array.isArray(group.hooks) ? group.hooks : [];
      for (const hook of hooks) {
        if (!isRecord(hook)) {
          continue;
        }
        entries.push({
          source,
          event,
          matcher,
          command: typeof hook.command === "string" ? hook.command : "",
          timeout: typeof hook.timeout === "number" ? hook.timeout : null,
        });
      }
    }
  }
  return entries;
}

/** matcher がこのツール名に当たるか */
export function matcherHits(matcher: string, tool: string): boolean {
  const trimmed = matcher.trim();
  if (trimmed === "" || trimmed === "*") {
    return true;
  }
  try {
    return new RegExp(`^(?:${trimmed})$`).test(tool);
  } catch {
    return trimmed === tool;
  }
}

/** このツール名で走る hook。ツール名を持つイベントは matcher で絞り、それ以外は常に含める */
export function hooksFor(entries: readonly HookEntry[], tool: string): HookEntry[] {
  return entries.filter((e) =>
    (TOOL_EVENTS as readonly string[]).includes(e.event) ? matcherHits(e.matcher, tool) : true,
  );
}

/** `.claude/settings.json` の env の 1 つ。無ければ空 */
export function envFromSettingsJson(text: string, key: string): string {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return "";
  }
  if (!isRecord(raw) || !isRecord(raw.env)) {
    return "";
  }
  const value = raw.env[key];
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
