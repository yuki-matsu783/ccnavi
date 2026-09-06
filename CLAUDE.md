# CLAUDE.md

## 挨拶と言語

- 日本語でやりとりすること
- 最初の挨拶は自然な日本語で返すこと
- ですますなどの丁寧な口調は不要

### 実行環境

- Windows の Git Bash、Windows の WSL、Claude Code on the web (Linux) の 3 つ。どれでも動くように書く
- 使える道具は `jq` 1.6、Node 22 (pnpm 10)、Python 3.12 (uv)、go。これ以外がある前提で書かない
- Windows と Linux で挙動が変わるところ (パス区切り、改行、シンボリックリンク、大文字小文字) は知見側に明記する