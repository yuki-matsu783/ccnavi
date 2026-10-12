---
type: skill-reference
title: macOS の tmp のパスを揃えて比べる
description: パスを含む文面をテストで比べるとき、macOS の tmp が /var と /private/var の 2 通りに見える差を realpath で揃える
tags: [process-test, macos]
keywords: [tmp, tempfile, realpath, /private/var, /var, macOS, パス, シンボリックリンク]
---

# macOS の tmp のパスを揃えて比べる

- パスを含む文面のテストは、期待値も実測も `os.path.realpath` で揃えてから比べる。macOS では tmp が `/var/...` と `/private/var/...` の 2 通りに見え、片方だけ解決された文面と素の文字列比較が食い違うため
- 一時ディレクトリの作り直しや比べる側の文字列にも同じ揃えをかける。片側だけ揃えると、macOS でだけ落ちて Linux では通る
