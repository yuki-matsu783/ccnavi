/**
 * パスやURLを、`/`の直後で折り返せるように出す（`<wbr>`を挟む）。幅が足りないときに名前の途中で折れるのを避ける。
 * 1つの区切りが行より長いときだけ、CSS（`overflow-wrap`）が途中で折る。
 */
import { Fragment, type JSX } from "react";

export function PathText({ text }: { readonly text: string }): JSX.Element {
  const parts = text.split("/");
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part}
          {index < parts.length - 1 ? (
            <>
              /<wbr />
            </>
          ) : null}
        </Fragment>
      ))}
    </>
  );
}
