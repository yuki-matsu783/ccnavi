/**
 * 設定画面の本体。見出しと、通信先・リポジトリ・PAT の 3 つの節。
 *
 * 通信先を足す・外すと、後ろの 2 つの節の選び先が変わるので、`revision` を上げて読み直させる。
 */
import { useState, type JSX } from "react";

import { Hosts } from "./Hosts.js";
import { Repos } from "./Repos.js";
import { Tokens } from "./Tokens.js";

export function App(): JSX.Element {
  const [revision, setRevision] = useState(0);
  return (
    <>
      <header className="toolbar">
        <div className="summary">
          <h1>設定</h1>
        </div>
      </header>
      <Hosts onChange={() => setRevision((r) => r + 1)} />
      <Repos revision={revision} />
      <Tokens revision={revision} />
    </>
  );
}
