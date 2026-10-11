/**
 * 設定画面の本体。見出しと、リポジトリ・PAT の 2 つの節。
 */
import type { JSX } from "react";

import { Repos } from "./Repos.js";
import { Tokens } from "./Tokens.js";

export function App(): JSX.Element {
  return (
    <>
      <header className="toolbar">
        <div className="summary">
          <h1>設定</h1>
        </div>
      </header>
      <Repos />
      <Tokens />
    </>
  );
}
