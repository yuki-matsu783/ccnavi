/**
 * バグ報告。サイドパネルの「バグ報告」で始め、ステータスバーの「終了」で zip にまとめる。
 *
 * 始めると「バグ発生時の挙動を再現してください」を出し、ffmpeg が見つかれば画面全体の録画を始める。
 * 途中はステータスバーの「スクリーンショット」のボタンか、ボードのビューのタイトルのカメラのボタン（通知の
 * 「スクリーンショット」も同じ）で何枚でも撮れる（ffmpeg は使わない）。通知は時間が経つと隠れるが、この 2 つは
 * 報告の間ずっと出ている。
 * 終了すると、録画を gif にし、始めてから `logs/diag/*.log` に足された行、スクリーンショット、
 * 説明（report.txt）を `logs/bug-report/bug-report-<時刻>.zip` にまとめる。
 * ffmpeg が無い・録画を始められないときは、録画だけをやめて残りはそのまま作る。
 *
 * 何を起動するかは core/bug-report.ts が組む。ここは子プロセスと VS Code の表示だけを持つ。
 * VS Code の API に触れるので単体テストの対象外。
 */
import { type ChildProcess, execFile, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import {
  buildZip,
  type Capture,
  captureKind,
  diagDir,
  ffmpegCandidates,
  gifArgs,
  logsSince,
  type LogMarks,
  markLogs,
  RECORD_MAX_SECONDS,
  recordArgs,
  reportStamp,
  reportText,
  screenshotCommand,
  type ZipEntry,
} from "./core/bug-report.js";
import * as diaglog from "./log.js";

/** 録画が始まったとみなすまで待つ時間。これより前に ffmpeg が終われば、始められなかったとみなす */
const START_GRACE_MS = 1500;
/** `q` を送ってから ffmpeg が書き終えるのを待つ時間。過ぎたら止める */
const STOP_WAIT_MS = 10_000;
/** 子プロセスのエラーの出力は末尾だけ残す */
const ERROR_TAIL = 2000;

interface Recording {
  readonly ffmpeg: string;
  readonly child: ChildProcess;
  readonly file: string;
  /** 終わっていれば終了コード */
  exit: number | null | undefined;
  stderr: string;
}

interface Session {
  readonly root: string;
  readonly capture: Capture;
  readonly work: string;
  readonly started: Date;
  readonly marks: LogMarks;
  recording: Recording | undefined;
  /** 録画を始める処理。終えるときはこれを待ってから録画を止める（始める途中の ffmpeg を取り残さない） */
  starting: Promise<void>;
  /** 拡張が止まった。始める途中で立っていれば、起こした ffmpeg をその場で止める */
  disposed: boolean;
  /** 録画しなかった・できなかった理由 */
  noRecording: string;
  /** 撮れた枚数と、撮り始めた回数（ファイル名の番号。連打しても名前がぶつからないよう撮る前に取る） */
  shots: number;
  seq: number;
  readonly errors: string[];
  readonly items: vscode.StatusBarItem[];
}

let session: Session | undefined;

function workspaceRoot(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
}

function procVersion(): string {
  try {
    return fs.readFileSync("/proc/version", "utf8");
  } catch {
    return "";
  }
}

function tail(text: string): string {
  return text.length > ERROR_TAIL ? text.slice(-ERROR_TAIL) : text;
}

/** 子プロセスを走らせ、終わるまで待つ。失敗したら標準エラーの末尾を載せて例外を投げる */
function run(file: string, args: readonly string[], timeout: number): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, [...args], { timeout, windowsHide: true, maxBuffer: 16 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`${path.basename(file)}: ${tail(String(stderr).trim()) || error.message}`));
        return;
      }
      resolve(String(stdout));
    });
  });
}

/** 子プロセスに渡すパス。WSL から Windows の道具を呼ぶときは、Windows の形（`\\wsl.localhost\…`）に直す */
async function toolPath(capture: Capture, file: string): Promise<string> {
  if (capture !== "wsl") {
    return file;
  }
  return (await run("wslpath", ["-w", file], 5000)).trim();
}

async function findFfmpeg(capture: Capture): Promise<string | undefined> {
  const configured = vscode.workspace.getConfiguration("ccnaviBoard").get<string>("ffmpegPath", "");
  for (const candidate of ffmpegCandidates(capture, configured)) {
    try {
      await run(candidate, ["-version"], 10_000);
      return candidate;
    } catch {
      // 次の候補へ
    }
  }
  return undefined;
}

async function startRecording(current: Session): Promise<void> {
  if (current.capture === "none") {
    current.noRecording = "この OS では録画しない（Windows・macOS・WSL だけ）";
    return;
  }
  const ffmpeg = await findFfmpeg(current.capture);
  if (ffmpeg === undefined) {
    current.noRecording = "ffmpeg が見つからない";
    void vscode.window.showWarningMessage(
      "ffmpeg が見つからないので、録画はしません。スクリーンショットとログは保存します。録画するには ffmpeg を入れるか、設定 ccnaviBoard.ffmpegPath に場所を書きます。",
    );
    return;
  }
  const file = path.join(current.work, "screen.mkv");
  const output = await toolPath(current.capture, file);
  if (current.disposed) {
    return;
  }
  const child = spawn(ffmpeg, recordArgs(current.capture, output), {
    stdio: ["pipe", "ignore", "pipe"],
    windowsHide: true,
  });
  // 止める直前に ffmpeg が落ちていると `q` の書き込みが EPIPE になる。拾わないと拡張ホストの例外になる
  child.stdin?.on("error", () => undefined);
  const recording: Recording = { ffmpeg, child, file, exit: undefined, stderr: "" };
  child.stderr?.on("data", (chunk: Buffer) => {
    recording.stderr = tail(recording.stderr + chunk.toString("utf8"));
  });
  child.on("error", (error) => {
    recording.stderr = tail(`${recording.stderr}\n${error.message}`);
    recording.exit = recording.exit ?? -1;
  });
  child.on("exit", (code) => {
    recording.exit = code;
  });
  await new Promise((resolve) => setTimeout(resolve, START_GRACE_MS));
  if (current.disposed) {
    halt(recording);
    return;
  }
  if (recording.exit !== undefined) {
    current.noRecording = "録画を始められなかった";
    current.errors.push(`ffmpeg（録画）: ${recording.stderr.trim()}`);
    const hint = current.capture === "mac" ? "macOS の「システム設定 > プライバシーとセキュリティ > 画面収録」で VS Code を許可してから、もう一度始めます。" : "";
    void vscode.window.showWarningMessage(`録画を始められなかったので、録画はしません。スクリーンショットとログは保存します。${hint}`);
    return;
  }
  current.recording = recording;
}

/** 待たずに止める（拡張を止めるとき）。WSL では kill が Windows 側に届かないことがあるので、先に `q` も送る */
function halt(recording: Recording): void {
  recording.child.stdin?.write("q");
  recording.child.stdin?.end();
  recording.child.kill();
}

/** 録画を止めて、書き終わるのを待つ */
async function stopRecording(recording: Recording): Promise<void> {
  if (recording.exit !== undefined) {
    return;
  }
  const exited = new Promise<void>((resolve) => recording.child.once("exit", () => resolve()));
  recording.child.stdin?.write("q");
  recording.child.stdin?.end();
  const timer = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), STOP_WAIT_MS));
  if ((await Promise.race([exited, timer])) === "timeout") {
    recording.child.kill();
    await exited;
  }
}

function statusItem(text: string, command: string, tooltip: string, priority: number): vscode.StatusBarItem {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, priority);
  item.text = text;
  item.command = command;
  item.tooltip = tooltip;
  item.show();
  return item;
}

/** 報告中か（ビューのタイトルのボタンの出し分け）。撮れない環境ではカメラのボタンを出さない */
async function setReporting(on: boolean, canShoot = false): Promise<void> {
  await vscode.commands.executeCommand("setContext", "ccnaviBoard.bugReporting", on);
  await vscode.commands.executeCommand("setContext", "ccnaviBoard.bugReportNoCapture", on && !canShoot);
}

/**
 * 再現を促す。押されたボタンに応じて、スクリーンショットを撮るか報告を終える。
 * 通知は閉じずに残ることがあるので、押されたときに別の報告へ替わっていれば何もしない。
 */
async function prompt(current: Session): Promise<void> {
  const shot = "スクリーンショット";
  const finish = "終了";
  const canShoot = current.capture !== "none";
  const picked = canShoot
    ? await vscode.window.showInformationMessage(
        "バグ発生時の挙動を再現してください。途中の画面は「スクリーンショット」で撮れます。終わったら「終了」を押します（ステータスバーにも同じボタンがあります）。",
        shot,
        finish,
      )
    : await vscode.window.showInformationMessage(
        "バグ発生時の挙動を再現してください。終わったら「終了」を押します（ステータスバーにも同じボタンがあります）。",
        finish,
      );
  if (session !== current) {
    return;
  }
  if (picked === shot) {
    await takeScreenshot();
  } else if (picked === finish) {
    await finishReport();
  }
}

export async function startReport(): Promise<void> {
  if (session !== undefined) {
    await prompt(session);
    return;
  }
  const root = workspaceRoot();
  const capture = captureKind(process.platform, process.env, procVersion());
  const current: Session = {
    root,
    capture,
    work: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-bug-report-")),
    started: new Date(),
    marks: root === "" ? new Map() : markLogs(diagDir(root)),
    recording: undefined,
    starting: Promise.resolve(),
    disposed: false,
    noRecording: "",
    shots: 0,
    seq: 0,
    errors: [],
    items: [
      statusItem("$(record) 再現中: 終了", "ccnaviBoard.bugReportFinish", "バグ報告を終えて zip にまとめる", 1001),
      statusItem("$(device-camera) スクリーンショット", "ccnaviBoard.bugReportScreenshot", "バグ報告: スクリーンショットを撮る", 1000),
    ],
  };
  current.items[0].backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
  if (capture === "none") {
    current.items[1].hide();
  }
  session = current;
  await setReporting(true, capture !== "none");
  diaglog.get("ccnavi-board", root).info("バグ報告を始めた", { capture });
  current.starting = startRecording(current).catch((error: unknown) => {
    current.noRecording = "録画を始められなかった";
    current.errors.push(`録画: ${error instanceof Error ? error.message : String(error)}`);
  });
  await current.starting;
  // 始める間に「終了」が押されていれば、促さない
  if (session === current) {
    void prompt(current);
  }
}

export async function takeScreenshot(): Promise<void> {
  const current = session;
  if (current === undefined) {
    return;
  }
  current.seq += 1;
  const file = path.join(current.work, `screenshot-${String(current.seq).padStart(2, "0")}.png`);
  try {
    const command = screenshotCommand(current.capture, await toolPath(current.capture, file));
    if (command === undefined) {
      void vscode.window.showWarningMessage("この OS ではスクリーンショットを撮りません（Windows・macOS・WSL だけ）。");
      return;
    }
    // 押したボタンの通知が消えるのを待ってから撮る
    await new Promise((resolve) => setTimeout(resolve, 300));
    await run(command.file, command.args, 20_000);
    if (!fs.existsSync(file)) {
      throw new Error("画像ができなかった");
    }
    current.shots += 1;
    vscode.window.setStatusBarMessage(`$(check) スクリーンショット ${current.shots} 枚目を撮りました`, 3000);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    current.errors.push(`スクリーンショット: ${message}`);
    const hint = current.capture === "mac" ? "（macOS では、「画面収録」で VS Code の許可が必要です）" : "";
    void vscode.window.showWarningMessage(`スクリーンショットを撮れませんでした${hint}: ${message}`);
  }
}

/** 録画を gif にする。できなければ理由を返す */
async function toGif(current: Session, recording: Recording): Promise<Buffer | string> {
  if (!fs.existsSync(recording.file) || fs.statSync(recording.file).size === 0) {
    current.errors.push(`ffmpeg（録画）: ${recording.stderr.trim()}`);
    return "録画のファイルができなかった";
  }
  const gif = path.join(current.work, "screen.gif");
  try {
    await run(recording.ffmpeg, gifArgs(await toolPath(current.capture, recording.file), await toolPath(current.capture, gif)), 10 * 60_000);
    return fs.readFileSync(gif);
  } catch (error) {
    current.errors.push(`ffmpeg（gif）: ${error instanceof Error ? error.message : String(error)}`);
    return "gif にできなかった";
  }
}

export async function finishReport(): Promise<void> {
  const current = session;
  if (current === undefined) {
    return;
  }
  session = undefined;
  current.items.forEach((item) => item.dispose());
  await setReporting(false);
  const finished = new Date();

  let zipFile: string;
  try {
    zipFile = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "バグ報告をまとめています…" },
      async (progress) => {
        // 録画を始める途中で押されたときは、始め終わるのを待ってから止める
        await current.starting;
        diaglog.get("ccnavi-board", current.root).info("バグ報告を終えた", { shots: current.shots, recorded: current.recording !== undefined });
        const entries: ZipEntry[] = [];
        let recordingNote = current.noRecording;
        if (current.recording !== undefined) {
          if (current.recording.exit !== undefined) {
            const why = current.recording.exit === 0 ? `上限の ${RECORD_MAX_SECONDS} 秒で止まった` : `終える前に止まった（終了コード ${String(current.recording.exit)}）`;
            current.errors.push(`ffmpeg（録画）: ${why} ${current.recording.stderr.trim()}`.trim());
          }
          progress.report({ message: "録画を止めています" });
          await stopRecording(current.recording);
          progress.report({ message: "録画を gif にしています" });
          const gif = await toGif(current, current.recording);
          if (typeof gif === "string") {
            recordingNote = gif;
          } else {
            entries.push({ name: "screen.gif", data: gif });
            recordingNote = "screen.gif";
          }
        }
        for (const name of fs.readdirSync(current.work).filter((name) => name.endsWith(".png")).sort()) {
          entries.push({ name: `screenshots/${name}`, data: fs.readFileSync(path.join(current.work, name)) });
        }
        const logs = current.root === "" ? new Map<string, Buffer>() : logsSince(diagDir(current.root), current.marks);
        for (const [name, data] of logs) {
          entries.push({ name: `logs/${name}`, data });
        }
        const extension = vscode.extensions.getExtension("local.ccnavi-board");
        const report = reportText({
          started: current.started,
          finished,
          vscodeVersion: vscode.version,
          extensionVersion: String((extension?.packageJSON as { version?: unknown } | undefined)?.version ?? ""),
          platform: `${process.platform} ${os.release()}`,
          capture: current.capture,
          recording: recordingNote,
          screenshots: current.shots,
          logs: [...logs.keys()],
          errors: current.errors,
        });
        entries.unshift({ name: "report.txt", data: Buffer.from(report, "utf8") });

        const outDir = current.root === "" ? os.tmpdir() : path.join(current.root, "logs", "bug-report");
        fs.mkdirSync(outDir, { recursive: true });
        const out = path.join(outDir, `bug-report-${reportStamp(current.started)}.zip`);
        fs.writeFileSync(out, buildZip(entries, finished), { mode: 0o600 });
        return out;
      },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    void vscode.window.showErrorMessage(`バグ報告の zip を書けませんでした: ${message}`);
    return;
  } finally {
    removeWork(current.work);
  }

  const reveal = "フォルダで表示";
  const picked = await vscode.window.showInformationMessage(
    `バグ報告を ${zipFile} に保存しました。録画とスクリーンショットには画面に映っていたもの（秘密の値も）が、ログにはその間の操作の記録が入るので、渡す前に中身を確かめてください。`,
    reveal,
  );
  if (picked === reveal) {
    await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(zipFile));
  }
}

/** 拡張を止めるときに録画が残っていれば止める（gif と zip は作らない） */
export function disposeReport(): void {
  const current = session;
  session = undefined;
  if (current === undefined) {
    return;
  }
  current.disposed = true;
  if (current.recording !== undefined) {
    halt(current.recording);
  }
  current.items.forEach((item) => item.dispose());
  removeWork(current.work);
}

/** 作業フォルダを消す。Windows で ffmpeg がまだ開いているなどで消せなければ残す（例外にしない） */
function removeWork(work: string): void {
  try {
    fs.rmSync(work, { recursive: true, force: true });
  } catch {
    // 一時フォルダなので OS に任せる
  }
}
