---
type: skill-reference
title: glab の入れ方
description: GitLab の認証に使う glab を、OS と状況（パッケージマネージャー・管理者権限）に合わせて入れる方法。1 つに絞って案内する
tags: [ccnavi-install, skills]
keywords: [glab, GitLab, winget, scoop, brew, apt, dnf, deb, rpm, PATH, not-on-path, too-old, git-credential]
---

# glab の入れ方

glab は必須ではない。入れられない・入れたくない人は、[auth.md](auth.md) の「glab を使わない代わりの道」を選んでよい。
ユーザに状況（OS・パッケージマネージャー・管理者権限）を確かめ、**コマンドは 1 つに絞って**示す。入れるのはユーザ。

入れ方の表の「`machine`」は、doctor の `machine` 行の値（`<os>-<arch>`）。

| OS | 条件 | 案内 |
|---|---|---|
| Windows | winget がある | `winget install --id GLab.GLab -e` |
| Windows | scoop がある | `scoop install glab` |
| Windows | どちらも無い・管理者権限が無い | リリースページから Windows 用の zip（`machine` の CPU に合うもの）を取り、`glab.exe` を `~/bin/` に置き、`~/.bashrc` に `export PATH="$HOME/bin:$PATH"` を足す |
| macOS | brew がある | `brew install glab` |
| macOS | brew が無い | リリースページの darwin 用 tar.gz を展開し、`glab` を `~/.local/bin/` に置いて PATH を通す。隔離の印（quarantine）が付いたら、外すかをユーザが決める |
| Linux / WSL | パッケージがある | `apt-cache policy glab`（dnf なら `dnf info glab`）で確かめ、あればそれで入れる |
| Linux / WSL | 管理者権限がある | リリースページの `.deb` を `sudo apt install ./<file>.deb`、`.rpm` を `sudo dnf install ./<file>.rpm` で入れる |
| Linux / WSL | 管理者権限が無い | リリースページの tar.gz を展開し、`glab` を `~/.local/bin/` に置いて PATH を通す |
| どれでも | 外のサイトから取れない | 社内の配布手順か管理者に頼んでもらう。それまでは [auth.md](auth.md) の代わりの道を使う |

- リリースページは `https://gitlab.com/gitlab-org/cli/-/releases`。ファイル名（版・OS・CPU）は決め打ちせず、ページで合うものを選んでもらう。
- WSL: Windows 側の `glab.exe` も相互運用で動くことはあるが、ccnavi での認証とパスの扱いを揃えるため、WSL の中に Linux 版を入れるよう案内する。

## doctor の `glab` 行ごとの案内

| 行 | 案内 |
|---|---|
| `todo not-installed` | 上の表で入れる。または代わりの道 |
| `todo not-on-path <パス>` | doctor が見つけたパスを示す。Claude Code を終了して新しい端末から起動し直す。それでも見つからなければ、そのフォルダを PATH に足す |
| `todo too-old <版>` | 入れたときと同じ方法で上げる。古い glab は `glab auth git-credential` を持たず、ccnavi が git に資格情報を渡せない |
| `warn … optional:` | glab 無しで origin と API に届いている。入れなくてよい |

入れたあとは、PATH が変わるので Claude Code を終了して新しい端末から起動し直してもらう。そのあとで認証（[auth.md](auth.md)）に進む。
