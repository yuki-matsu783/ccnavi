/**
 * Go の WASM（GOOS=js）が読み書きする、メモリの上のファイルシステム。Go の `syscall/fs_js.go` は `globalThis.fs` の
 * Node の fs の形（コールバックの API）を呼ぶので、その形だけを持つ。コールバックはその場で呼ぶ（Go の側は
 * 待ち合わせをしない。非同期にすると、イベントループを止めている Go の呼び出しが返らない）。
 *
 * 置くのは判定の仮のツリーだけ（拡張は ccnavi に要求ごとに組み直させる）。権限は見ない。シンボリックリンクは
 * 辿るが、仮のツリーには置かない。
 */

const S_IFMT = 0o170000;
const S_IFDIR = 0o040000;
const S_IFREG = 0o100000;
const S_IFLNK = 0o120000;
/** 辿るシンボリックリンクの上限（Linux の MAXSYMLINKS と同じ） */
const MAX_LINKS = 40;

/** Go が Node の値として読む開き方の値（Linux と同じ） */
const constants = {
  O_RDONLY: 0,
  O_WRONLY: 1,
  O_RDWR: 2,
  O_CREAT: 0o100,
  O_EXCL: 0o200,
  O_TRUNC: 0o1000,
  O_APPEND: 0o2000,
  O_DIRECTORY: 0o200000,
};

interface Node {
  mode: number;
  ino: number;
  data: Uint8Array;
  size: number;
  children: Map<string, Node> | null;
  target: string;
  mtimeMs: number;
  atimeMs: number;
  ctimeMs: number;
  nlink: number;
}

interface Open {
  node: Node;
  flags: number;
  pos: number;
}

type Callback<T = unknown> = (err: FsError | null, value?: T) => void;

/**
 * 失敗の値。Go が読むのは `code` だけ。Error にしないのは、作るたびにスタックを取ると遅いため（無いファイルの stat は
 * 判定 1 回に数百回ある）
 */
class FsError {
  readonly message: string;
  constructor(
    readonly code: string,
    op: string,
    path: string,
  ) {
    this.message = `${code}: ${op} '${path}'`;
  }
}

function split(path: string): string[] {
  const out: string[] = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return out;
}

export interface MemFs {
  readonly constants: typeof constants;
  /** 中身をすべて捨てる */
  reset(): void;
  [name: string]: unknown;
}

/** 新しい空のファイルシステム。`globalThis.fs` に置いて Go に渡す */
export function createMemFs(log: (text: string) => void = (t) => console.log(t)): MemFs {
  let nextIno = 1;
  const now = () => Date.now();
  const make = (mode: number, perm: number): Node => {
    const t = now();
    return {
      mode: mode | (perm & 0o7777),
      ino: nextIno++,
      data: new Uint8Array(0),
      size: 0,
      children: mode === S_IFDIR ? new Map() : null,
      target: "",
      mtimeMs: t,
      atimeMs: t,
      ctimeMs: t,
      nlink: 1,
    };
  };
  let root = make(S_IFDIR, 0o755);
  const fds = new Map<number, Open>();
  let nextFd = 3;
  const out: Record<number, string> = { 1: "", 2: "" };

  const isDir = (n: Node) => (n.mode & S_IFMT) === S_IFDIR;
  const isLink = (n: Node) => (n.mode & S_IFMT) === S_IFLNK;

  /** パスを辿る。follow が偽なら最後の段のリンクは辿らない。答えは親と名前と、在ればその節 */
  function walk(path: string, follow: boolean, op: string, depth = 0): { parent: Node; name: string; node: Node | undefined } {
    if (depth > MAX_LINKS) throw new FsError("ELOOP", op, path);
    const parts = split(path);
    let dir = root;
    const done: string[] = [];
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      const last = i === parts.length - 1;
      if (!isDir(dir)) throw new FsError("ENOTDIR", op, path);
      const node = dir.children!.get(name);
      if (last) {
        if (node && isLink(node) && follow) {
          const next = node.target.startsWith("/") ? node.target : `/${[...done, node.target].join("/")}`;
          return walk(next, true, op, depth + 1);
        }
        return { parent: dir, name, node };
      }
      if (!node) throw new FsError("ENOENT", op, path);
      if (isLink(node)) {
        const next = node.target.startsWith("/") ? node.target : `/${[...done, node.target].join("/")}`;
        const rest = parts.slice(i + 1).join("/");
        return walk(`${next}/${rest}`, follow, op, depth + 1);
      }
      done.push(name);
      dir = node;
    }
    return { parent: root, name: "", node: root };
  }

  function existing(path: string, follow: boolean, op: string): Node {
    const { node } = walk(path, follow, op);
    if (!node) throw new FsError("ENOENT", op, path);
    return node;
  }

  function fd(n: number, op: string): Open {
    const f = fds.get(n);
    if (!f) throw new FsError("EBADF", op, String(n));
    return f;
  }

  function stats(n: Node) {
    return {
      dev: 1,
      ino: n.ino,
      mode: n.mode,
      nlink: n.nlink,
      uid: 0,
      gid: 0,
      rdev: 0,
      size: isLink(n) ? n.target.length : n.size,
      blksize: 4096,
      blocks: Math.ceil(n.size / 512),
      atimeMs: n.atimeMs,
      mtimeMs: n.mtimeMs,
      ctimeMs: n.ctimeMs,
      isDirectory: () => isDir(n),
    };
  }

  function resize(n: Node, size: number) {
    if (size > n.data.length) {
      const grown = new Uint8Array(Math.max(size, n.data.length * 2, 64));
      grown.set(n.data.subarray(0, n.size));
      n.data = grown;
    } else if (size < n.size) {
      n.data.fill(0, size, n.size);
    }
    n.size = size;
  }

  function writeOut(n: number, buf: Uint8Array): number {
    out[n] += new TextDecoder().decode(buf);
    const nl = out[n].lastIndexOf("\n");
    if (nl >= 0) {
      log(out[n].slice(0, nl));
      out[n] = out[n].slice(nl + 1);
    }
    return buf.length;
  }

  /** 同期の処理をコールバックの形にする（その場で呼ぶ） */
  const wrap =
    <A extends unknown[], T>(body: (...args: A) => T) =>
    (...args: [...A, Callback<T>]) => {
      const cb = args.pop() as Callback<T>;
      let value: T;
      try {
        value = body(...(args as unknown as A));
      } catch (err) {
        cb(err as FsError);
        return;
      }
      cb(null, value);
    };

  const fs: MemFs = {
    constants,
    reset() {
      root = make(S_IFDIR, 0o755);
      fds.clear();
    },
    writeSync: (n: number, buf: Uint8Array) => writeOut(n, buf),
    open: wrap((path: string, flags: number, mode: number) => {
      const { parent, name, node } = walk(path, true, "open");
      let target = node;
      if (target && flags & constants.O_CREAT && flags & constants.O_EXCL) throw new FsError("EEXIST", "open", path);
      if (!target) {
        if (!(flags & constants.O_CREAT)) throw new FsError("ENOENT", "open", path);
        if (!isDir(parent)) throw new FsError("ENOTDIR", "open", path);
        target = make(S_IFREG, mode);
        parent.children!.set(name, target);
        parent.mtimeMs = now();
      }
      const writing = (flags & 3) !== constants.O_RDONLY;
      if (isDir(target) && writing) throw new FsError("EISDIR", "open", path);
      if (!isDir(target) && flags & constants.O_DIRECTORY) throw new FsError("ENOTDIR", "open", path);
      if (writing && flags & constants.O_TRUNC) {
        resize(target, 0);
        target.mtimeMs = now();
      }
      const n = nextFd++;
      fds.set(n, { node: target, flags, pos: 0 });
      return n;
    }),
    close: wrap((n: number) => {
      fd(n, "close");
      fds.delete(n);
    }),
    read: wrap((n: number, buf: Uint8Array, offset: number, length: number, position: number | null) => {
      const f = fd(n, "read");
      if (isDir(f.node)) throw new FsError("EISDIR", "read", String(n));
      const at = position ?? f.pos;
      const count = Math.max(0, Math.min(length, f.node.size - at));
      buf.set(f.node.data.subarray(at, at + count), offset);
      if (position === null || position === undefined) f.pos += count;
      return count;
    }),
    write: wrap((n: number, buf: Uint8Array, offset: number, length: number, position: number | null) => {
      if (n === 1 || n === 2) return writeOut(n, buf.subarray(offset, offset + length));
      const f = fd(n, "write");
      if ((f.flags & 3) === constants.O_RDONLY) throw new FsError("EBADF", "write", String(n));
      const at = f.flags & constants.O_APPEND ? f.node.size : (position ?? f.pos);
      if (at + length > f.node.size) resize(f.node, at + length);
      f.node.data.set(buf.subarray(offset, offset + length), at);
      f.node.mtimeMs = now();
      if (position === null || position === undefined) f.pos = at + length;
      return length;
    }),
    fstat: wrap((n: number) => stats(fd(n, "fstat").node)),
    stat: wrap((path: string) => stats(existing(path, true, "stat"))),
    lstat: wrap((path: string) => stats(existing(path, false, "lstat"))),
    mkdir: wrap((path: string, perm: number) => {
      const { parent, name, node } = walk(path, false, "mkdir");
      if (node || name === "") throw new FsError("EEXIST", "mkdir", path);
      if (!isDir(parent)) throw new FsError("ENOTDIR", "mkdir", path);
      parent.children!.set(name, make(S_IFDIR, perm));
      parent.mtimeMs = now();
    }),
    readdir: wrap((path: string) => {
      const node = existing(path, true, "readdir");
      if (!isDir(node)) throw new FsError("ENOTDIR", "readdir", path);
      return [...node.children!.keys()].sort();
    }),
    unlink: wrap((path: string) => {
      const { parent, name, node } = walk(path, false, "unlink");
      if (!node) throw new FsError("ENOENT", "unlink", path);
      if (isDir(node)) throw new FsError("EISDIR", "unlink", path);
      parent.children!.delete(name);
      parent.mtimeMs = now();
    }),
    rmdir: wrap((path: string) => {
      const { parent, name, node } = walk(path, false, "rmdir");
      if (!node) throw new FsError("ENOENT", "rmdir", path);
      if (!isDir(node)) throw new FsError("ENOTDIR", "rmdir", path);
      if (node.children!.size > 0) throw new FsError("ENOTEMPTY", "rmdir", path);
      if (node === root) throw new FsError("EBUSY", "rmdir", path);
      parent.children!.delete(name);
      parent.mtimeMs = now();
    }),
    rename: wrap((from: string, to: string) => {
      const src = walk(from, false, "rename");
      if (!src.node) throw new FsError("ENOENT", "rename", from);
      const dst = walk(to, false, "rename");
      if (!isDir(dst.parent)) throw new FsError("ENOTDIR", "rename", to);
      if (dst.node === src.node) return;
      if (dst.node) {
        if (isDir(src.node) && !isDir(dst.node)) throw new FsError("ENOTDIR", "rename", to);
        if (!isDir(src.node) && isDir(dst.node)) throw new FsError("EISDIR", "rename", to);
        if (isDir(dst.node) && dst.node.children!.size > 0) throw new FsError("ENOTEMPTY", "rename", to);
      }
      src.parent.children!.delete(src.name);
      dst.parent.children!.set(dst.name, src.node);
      src.parent.mtimeMs = dst.parent.mtimeMs = now();
    }),
    chmod: wrap((path: string, mode: number) => {
      const node = existing(path, true, "chmod");
      node.mode = (node.mode & S_IFMT) | (mode & 0o7777);
    }),
    fchmod: wrap((n: number, mode: number) => {
      const node = fd(n, "fchmod").node;
      node.mode = (node.mode & S_IFMT) | (mode & 0o7777);
    }),
    chown: wrap(() => undefined),
    fchown: wrap(() => undefined),
    lchown: wrap(() => undefined),
    utimes: wrap((path: string, atime: number, mtime: number) => {
      const node = existing(path, true, "utimes");
      node.atimeMs = atime * 1000;
      node.mtimeMs = mtime * 1000;
    }),
    truncate: wrap((path: string, length: number) => {
      const node = existing(path, true, "truncate");
      if (isDir(node)) throw new FsError("EISDIR", "truncate", path);
      resize(node, length);
    }),
    ftruncate: wrap((n: number, length: number) => resize(fd(n, "ftruncate").node, length)),
    fsync: wrap(() => undefined),
    readlink: wrap((path: string) => {
      const node = existing(path, false, "readlink");
      if (!isLink(node)) throw new FsError("EINVAL", "readlink", path);
      return node.target;
    }),
    symlink: wrap((target: string, path: string) => {
      const { parent, name, node } = walk(path, false, "symlink");
      if (node) throw new FsError("EEXIST", "symlink", path);
      const link = make(S_IFLNK, 0o777);
      link.target = target;
      parent.children!.set(name, link);
    }),
    link: wrap((existingPath: string, path: string) => {
      const node = existing(existingPath, false, "link");
      if (isDir(node)) throw new FsError("EPERM", "link", existingPath);
      const { parent, name, node: there } = walk(path, false, "link");
      if (there) throw new FsError("EEXIST", "link", path);
      node.nlink++;
      parent.children!.set(name, node);
    }),
  };
  return fs;
}
