/**
 * 「hook」のタブ。`.claude/settings.json`（と `settings.local.json`）に登録された hook を並べる。
 * **表示するだけで書き換えない。** 利用者ごとの設定（`~/.claude/settings.json`）は拡張ホストが読まない。
 */
import type { JSX } from "react";

import type { HookEntry } from "../../core/hooks.js";

export interface HooksProps {
  readonly hooks: readonly HookEntry[];
  readonly files: { readonly settings: boolean; readonly settingsLocal: boolean };
}

export function Hooks({ hooks, files }: HooksProps): JSX.Element {
  return (
    <>
      <p className="hint">
        表示するだけで書き換えない。対象は <code>.claude/settings.json</code>
        {files.settingsLocal && (
          <>
            {" と "}
            <code>.claude/settings.local.json</code>
          </>
        )}{" "}
        の hooks。利用者ごとの設定（<code>~/.claude/settings.json</code>）は対象外。
      </p>
      <Table hooks={hooks} files={files} />
    </>
  );
}

function Table({ hooks, files }: HooksProps): JSX.Element {
  if (!files.settings) {
    return <p className="empty">.claude/settings.json が無い</p>;
  }
  if (hooks.length === 0) {
    return <p className="empty">hooks が 1 つも登録されていない</p>;
  }
  return (
    <>
      <table className="hooks">
        <thead>
          <tr>
            <th>イベント</th>
            <th>matcher</th>
            <th>コマンド</th>
            <th>timeout</th>
            <th>定義元</th>
          </tr>
        </thead>
        <tbody>
          {hooks.map((hook, index) => (
            <tr key={index}>
              <td>{hook.event}</td>
              <td>
                <code>{hook.matcher === "" ? "（全部）" : hook.matcher}</code>
              </td>
              <td className="cmd">
                <code>{hook.command}</code>
              </td>
              <td className="num">{hook.timeout === null ? "" : String(hook.timeout)}</td>
              <td>{hook.source === "settings" ? "settings.json" : "settings.local.json"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        「判定を試す」でツールを選ぶと、そのツールで実行される hook だけをここから絞り込んで出す。matcher の意味は Claude Code のもの（空か <code>*</code> で全部、それ以外はツール名への正規表現）。
      </p>
    </>
  );
}
