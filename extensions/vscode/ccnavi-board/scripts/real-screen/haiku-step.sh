#!/bin/sh
# チケット作業の手順を haiku に `claude -p` で 1 手ずつ進めさせ、動きを読みやすく要約する。
# 手順の文書が、その手順を知らないエージェントにどこまで通じるかを見るための道具。
#
#   sh haiku-step.sh <作業するリポジトリ> <出力ディレクトリ> <依頼文のファイル> [--continue]
#
# 1 回目は依頼文（例: 「issue #1 を進めてください」）、2 回目以降は --continue と、ユーザ役の一言
# （「承認しました。続きを進めてください。」など）を渡す。承認は、ユーザ役が実画面（approve.js）で行う。
# 出力: <出力ディレクトリ>/step-<連番>.jsonl（生の記録）と、標準出力の要約（summarize-run.py）。
# 起動に要る環境（fake-host.sh の PATH と GITLAB_TOKEN）は、呼ぶ側で設定しておく。
# 許可は acceptEdits に、使うツールだけを --allowedTools で足す（bypassPermissions は使わない）。
# 上限は CCNAVI_STEP_TIMEOUT 秒（既定 1500）。
set -eu

repo=${1:?作業するリポジトリ}
out=${2:?出力ディレクトリ}
prompt=${3:?依頼文のファイル}
cont=${4:-}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
n=$(($(ls "$out"/step-*.jsonl 2>/dev/null | wc -l) + 1))
log="$out/step-$n.jsonl"
flag=
[ "$cont" != --continue ] || flag=--continue

cd "$repo"
# shellcheck disable=SC2086
timeout "${CCNAVI_STEP_TIMEOUT:-1500}" claude -p $flag --model haiku --permission-mode acceptEdits \
	--allowedTools "Bash,Read,Write,Edit,Glob,Grep,Agent" --output-format stream-json --verbose \
	<"$prompt" >"$log" 2>"$out/step-$n.err" || echo "claude の終了コード: $?" >&2
python3 "$here/summarize-run.py" "$log" "${CCNAVI_SUMMARY_WIDTH:-350}"
