/**
 * 1 つのリポジトリの欄ごとの ErrorBoundary。欄を描く途中で投げても、その欄だけを短いエラー表示（`notice error`）に
 * 替え、ほかの欄は残す（境界が無いと React がルートごと外し、購読も 0 件になって戻らない）。
 *
 * `section` は controller が欄を作り直すたびに替わる。替わったら（読み直しで snapshot が変わったら）エラーを外して描き直す。
 */
import { Component, type ErrorInfo, type JSX, type ReactNode } from "react";

import { Notice } from "./parts.js";

export interface RepoBoundaryProps {
  /** `owner/repo` */
  readonly name: string;
  readonly repoKey: string;
  /** 欄の材料。替わったら描き直しを試す */
  readonly section: unknown;
  readonly children: ReactNode;
}

interface State {
  readonly error: unknown;
  readonly failed: boolean;
  readonly section: unknown;
}

export class RepoBoundary extends Component<RepoBoundaryProps, State> {
  state: State = { error: null, failed: false, section: this.props.section };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error, failed: true };
  }

  static getDerivedStateFromProps(props: RepoBoundaryProps, state: State): Partial<State> | null {
    if (props.section === state.section) return null;
    return { error: null, failed: false, section: props.section };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(`${this.props.name} の欄を描けなかった`, error, info.componentStack);
  }

  render(): JSX.Element | ReactNode {
    if (!this.state.failed) return this.props.children;
    const why = (this.state.error as Error | null)?.message ?? String(this.state.error);
    return (
      <section className="repo" data-repo={this.props.repoKey} data-render-error="true">
        <header className="repo-head">
          <h2>{this.props.name}</h2>
        </header>
        <Notice kind="error" text={`${this.props.name} の欄を表示できませんでした：${why}`} />
      </section>
    );
  }
}
