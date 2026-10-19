/**
 * 画面を覆う背景と、その上の本体。承認（`Approval`）・保存前の確かめ（`SaveReview`・`Review`）・エージェントの提案の確認
 * （`Proposal`）が使い、`role="dialog"`・`aria-modal`・`aria-labelledby` と見た目（`Dialog.css`）を 1 か所に持つ。
 * 古いクラス名（`backdropClass`・`cardClass`）は、画面の幅の指定とテストの目印に残す。
 */
import type { JSX, ReactNode, Ref } from "react";

export interface DialogProps {
  /** 見出しの `id`（`aria-labelledby` に使う） */
  readonly titleId: string;
  /** 背景に足すクラス（画面ごとの目印） */
  readonly backdropClass: string;
  /** 本体に足すクラス（画面ごとの目印と幅の指定） */
  readonly cardClass: string;
  readonly id?: string;
  /** 案内の指す先（`data-tour`） */
  readonly tour?: string;
  /** 背景に付ける `data-*`（承認の種類など） */
  readonly backdropData?: Readonly<Record<string, string>>;
  readonly cardRef?: Ref<HTMLElement>;
  readonly children: ReactNode;
}

export function Dialog({ titleId, backdropClass, cardClass, id, tour, backdropData, cardRef, children }: DialogProps): JSX.Element {
  return (
    <div className={`overlay-backdrop ${backdropClass}`} {...backdropData}>
      <section className={`overlay ${cardClass}`} id={id} data-tour={tour} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={cardRef}>
        {children}
      </section>
    </div>
  );
}

/** 操作ボタンの列。右寄せで、狭ければ折り返す */
export function DialogActions({ children, className = "", tour }: { readonly children: ReactNode; readonly className?: string; readonly tour?: string }): JSX.Element {
  return (
    <div className={`overlay-actions ${className}`.trim()} data-tour={tour}>
      {children}
    </div>
  );
}
