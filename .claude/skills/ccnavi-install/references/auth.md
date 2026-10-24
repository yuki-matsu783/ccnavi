---
type: skill-reference
title: GitLab の認証と glab を使わない代わりの道
description: ccnavi が GitLab に届く仕組み（git の資格情報と API）と、glab でログインする方法、glab を使わない代わりの道、トークンの置き場
tags: [ccnavi-install, skills]
keywords: [認証, GitLab, glab auth login, GITLAB_TOKEN, トークン, 資格情報, ssh, 社内CA, sslCAInfo, remote, host-api, origin]
---

# 認証と、glab を使わない代わりの道

doctor の `remote`・`host-api`・`source-remote` が todo のとき、`origin` が `warn ssh` のときに読む。
トークンやパスワードは会話に出させない、書かせない。ログインの操作は別のターミナルでユーザに打ってもらう。

## ccnavi の仕組み

- **git の認証**: origin が http か https のとき、ccnavi は git の資格情報ヘルパーに glab（GitHub 系は gh）を足す。
  足し方は環境変数で、`.gitconfig` と origin の URL は書き換えない。ユーザがもともと持っているヘルパーが先に使われる。
  glab は絶対パスで解ける必要があり、パスに `"`・`$`・`` ` ``・`\` を含むと足さない。
  **ログインしているかは確かめない**。ログインしていなければ、ヘルパーを足しても git の認証は通らない
- **API の認証**: ccnavi はまず `glab api` でプロジェクトを読んでみる。通らなければ curl に切り替える。
  curl のときは、curl と環境変数 `GITLAB_TOKEN` があるかしか見ない（通信して確かめない）。
  doctor の `host-api` は、curl のときは API を実際に打って確かめる
- origin が ssh の形（`git@host:…`）のとき、glab の資格情報は足されない。ssh キーで通る。API だけは glab か `GITLAB_TOKEN` が要る。
  https に替えるかはユーザが決める

## glab でログインする

ユーザに**別のターミナル**で打ってもらう。

```sh
glab auth login --hostname <host[:port]>
```

- 認証方法は Token、プロトコルは HTTPS を選ぶ
- 「git の認証にも glab を使うか」は、Yes にすると glab がグローバルの `.gitconfig` に設定を書く。ccnavi は自分で足すので No でも動く。どちらにするかはユーザが決める
- トークンは `https://<host>/-/user_settings/personal_access_tokens`（古い GitLab は `/-/profile/personal_access_tokens`）で作る。スコープは `api` と `write_repository`

## 社内 CA

社内の認証局の証明書を使うホストでは、次を設定する（ユーザが打つ）。

- glab: `glab config set ca_cert <pem> --host <host>`
- git: そのホストだけに `git config --global http.https://<host>/.sslCAInfo <pem>`
- Windows: `git config --get http.sslBackend` を確かめ、`schannel` なら OS の証明書ストアに入っていれば設定は要らない

## glab を使わない代わりの道

glab を入れられない・入れたくないとき、次で足りる。

1. **fetch と push**: git の資格情報（Git Credential Manager・キーチェーン・SSH キー）で通る。一度、ターミナルで `git -C <ws> fetch origin` を打って認証を済ませてもらう
2. **レビュー（API）**: curl と環境変数 `GITLAB_TOKEN` が要る
3. 確かめる: `doctor --online` で `remote` と `host-api` が ok になれば、glab は入れなくてよい。`glab` 行は `warn … optional:` になる

注意点は次のとおり。

- `remote` の ok は「**読める**」ことだけを意味する。push の権限は確かめていない。最初の push で分かる
- `host-api` のコードは、`unauthorized`（トークンが通らない）、`not-found`（パスか権限）、`unreachable`（接続・TLS）、`timeout`、`token-missing`（`GITLAB_TOKEN` が無い）、`no-curl`、`no-jq`

## `GITLAB_TOKEN` の置き場

- `.claude/settings.json` の `env` には**書かない**（git で共有される）
- OS のユーザ環境変数か、追跡されない `.claude/settings.local.json` の `env` に置き、Claude Code を起動し直す
- トークンの値は会話に出させない。ユーザが自分で設定する
