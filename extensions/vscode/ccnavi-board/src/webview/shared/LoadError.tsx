/**
 * 読み込めなかった画面の共通部品。理由と、読めなかったファイル（分かるとき）、操作の［エディタで開く］［更新］を並べる。
 * 設定の3画面は、対象の切り替え（`children`）を理由の上に置く。
 */
import type { JSX, ReactNode } from "react";

export function LoadError({
  heading,
  error,
  file,
  busy,
  onReload,
  onOpen,
  children,
}: {
  readonly heading: string;
  readonly error: string;
  /** 読めなかったファイルの相対パス。あれば表示し、［エディタで開く］を出す */
  readonly file?: string;
  readonly busy?: boolean;
  readonly onReload: () => void;
  readonly onOpen?: () => void;
  readonly children?: ReactNode;
}): JSX.Element {
  return (
    <>
      <p className="empty">{heading}</p>
      {children}
      {file === undefined ? null : (
        <p className="load-error-file">
          ファイル: <code>{file}</code>
        </p>
      )}
      <pre className="load-error">{error}</pre>
      <div className="load-error-actions">
        {file !== undefined && onOpen !== undefined ? (
          <button type="button" className="action" data-action="open-file" title="読めなかったファイルをエディタで開きます" onClick={onOpen}>
            エディタで開く
          </button>
        ) : null}
        <button type="button" className="action" data-action="reload" title="ファイルを更新します" disabled={busy} onClick={onReload}>
          更新
        </button>
      </div>
    </>
  );
}
