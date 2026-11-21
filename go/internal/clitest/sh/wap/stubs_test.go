package wap

import (
	"path/filepath"
	"strings"

	"ccnavi/internal/harness/exe"
)

// 実行ファイルの代わりと git の代わりの sh。@名前@ を fill で差し込む。

// exeWrapper は実行ファイルの代わり。実行ファイル（exe.Path）を起こす。
// `user` の入口は、ラッパーのオプション（--root など）の後の最初の語。
// ターミナル要求を切るオプションを前に置くと読み替わらないので、`user` の呼び出しでは後ろに付ける
// （ほかは `--` を挟む呼び出しがあるので前に付ける）。
const exeWrapper = `#!/bin/sh
` + userEntry + `
if [ "$first" = user ]; then
  exec '@EXE@' "$@" --guard-ticket-approval disable
fi
exec '@EXE@' --guard-ticket-approval disable "$@"
`

// userEntry はラッパーのオプションを読み飛ばして、最初の語を first に入れる部分。
const userEntry = `skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --record-commit | --record-merge) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  --record-commit=* | --record-merge=*) ;;
  *) first="$a"; break ;;
  esac
done`

// lostReply は git の代わり。push だけ、実際の push を済ませてから失敗したふりをする（応答だけが失われた形）。
const lostReply = `#!/bin/sh
for a in "$@"; do
  if [ "$a" = push ]; then
    '@GIT@' "$@" >/dev/null 2>&1
    echo 'fatal: the remote end hung up unexpectedly' >&2
    exit 128
  fi
done
exec '@GIT@' "$@"
`

// urlGit は git の代わり。remote get-url origin だけをホストのスタブの URL で答え、残りは実際のものに渡す
// （取り込みと push は bare のリモート、ホストの API はスタブへ）。
const urlGit = `#!/bin/sh
case " $* " in
*" remote get-url origin "*) printf '%s\n' '@URL@'; exit 0 ;;
esac
exec '@GIT@' "$@"
`

// wapPrelude はスタブの頭に置く部分。write-and-push の sh は、書く・コミットするまでを実行ファイルの 1 回の起動
// （--record-commit <文>。答えは <一覧>.answer）に任せる。スタブは書くところだけを代わりにするので、--record-commit が来たら、
// それを外して自分をもう一度起こし（書く）、書いた一覧を本物の実行ファイルの write-and-push commit でコミットして答えを書く。
const wapPrelude = `if [ -z "${WAP_STUB_INNER:-}" ]; then
  case " $* " in *" --record-commit "*)
    wap_msg=""; wap_list=""; wap_tree=""; wap_root=""; wap_prev=""
    for wap_a in "$@"; do
      shift
      case "$wap_prev" in
      --record-commit) wap_msg="$wap_a"; wap_prev=""; continue ;;
      --record-merge) wap_prev=""; continue ;;
      --record-writes) wap_list="$wap_a" ;;
      --record-tree) wap_tree="$wap_a" ;;
      --root) wap_root="$wap_a" ;;
      esac
      wap_prev="$wap_a"
      case "$wap_a" in --record-commit | --record-merge) continue ;; esac
      set -- "$@" "$wap_a"
    done
    wap_base=$(git -C "$wap_tree" rev-parse HEAD)
    wap_rc=0
    WAP_STUB_INNER=1 "$0" "$@" || wap_rc=$?
    printf 'write-and-push 1\n' >"$wap_list.answer"
    [ "$wap_rc" -eq 0 ] || exit "$wap_rc"
    [ -f "$wap_list" ] || : >"$wap_list"
    wap_out=$('@EXE@' --root "$wap_root" write-and-push commit \
      "$wap_tree" "$wap_list" "$wap_msg")
    wap_sha=$(printf '%s\n' "$wap_out" | sed -n 's/^commit //p')
    [ -n "$wap_sha" ] || exit 1
    printf 'push yes\ntree %s\nbranch %s\nbase %s\n' \
      "$wap_tree" "$(git -C "$wap_tree" symbolic-ref --short HEAD)" "$wap_base" \
      >>"$wap_list.answer"
    printf 'commit %s\nhead %s\nlist %s\n' "$wap_sha" \
      "$(git -C "$wap_tree" rev-parse HEAD)" "$wap_list" >>"$wap_list.answer"
    exit 0 ;;
  esac
fi
`

// acceptPlanCase は --accept-plan のスタブ。実行ファイルと同じく、操作記録を作って、その操作ID を計画に書く
// （teststub oprecord。記録と計画の中身は acceptPlanSpec）。
const acceptPlanCase = `*" --accept-plan "*)
  plan=""; root=""; prev=""
  for a in "$@"; do
    [ "$prev" = --accept-plan ] && plan="$a"
    [ "$prev" = --root ] && root="$a"
    prev="$a"; done
  '@STUB@' oprecord "$root/logs/state" "$plan" '@SPEC@'
  exit 0 ;;
`

const acceptPlanSpec = `{"begin": {"kind": "decide", "repo": {"host": "test", "mr_url": "u/mr"}, "parent": "i0001", "phase": 1, "mr": 1,
 "plan": {"digest": ""}, "stages": [["reply", "reply"], ["note", "comment"]]},
 "plan": {"version": 1, "digest": "", "threads": []}}
`

// half は実行ファイルの半分のスタブ。残った指摘の行き先（user reviewed ... --yes）だけを代わりに書き
// （親のワークツリーにレビュー済みマーカーを置き、書いたパスの一覧を出し、答えの JSON と下書きを書く）、
// 残り（write-and-push family・write-and-push sort など）は実際のものに渡す。
const half = `#!/bin/sh
@PRELUDE@case " $* " in
@PLAN_CASE@*" user reviewed "*" --yes "*)
  list=""; tree=""; root=""; done_plan=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
    --accept-done) done_plan="$2"; shift 2 ;;
    --record-writes) list="$2"; shift 2 ;;
    --record-tree) tree="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    *) shift ;;
    esac
  done
  rel=.ccnavi/approved/phases/i0001/1.reviewed
  mkdir -p "$tree/.ccnavi/approved/phases/i0001"
  printf '{"mr": 1, "accepted": ["u1"], "at": "t"}' >"$tree/$rel"
  [ -z "$list" ] || printf '%s\n' "$rel" >"$list"
  mkdir -p "$root/logs/state"
  op=$(sed -n 's/.*"op": "\([0-9a-f]*\)".*/\1/p' "$done_plan" 2>/dev/null)
  [ -z "$op" ] || '@EXE@' --root "$root" ops applied "$op" local true
  printf '<!-- ccnavi:decide -->\n決めた\n' >"$root/logs/state/review-decide-i0001-1.md"
  printf '{"version":1,"ok":true,"reviewed":true,"followup":""}\n'
  exit 0 ;;
esac
` + userEntry + `
if [ "$first" = user ]; then
  exec '@EXE@' "$@" --guard-ticket-approval disable
fi
exec '@EXE@' --guard-ticket-approval disable "$@"
`

// hostHalf は実行ファイルのスタブ（ホストに触る副命令の試験用）。状態を書く副命令だけを代わりに書き
// （親のワークツリーにマーカーを置き、書いたパスの一覧を出す）、write-and-push・sync などは実際のものに渡す。
// HALF_FAIL にファイル名があれば、review requested を最初の 1 回だけ失敗させる（打ち直しの試験）。
var hostHalf = `#!/bin/sh
@PRELUDE@list=""; tree="${HALF_TREE:-}"; root=""
i=0
for a in "$@"; do
  case "$prev" in
  --record-writes) list="$a" ;;
  --record-tree) tree="$a" ;;
  --root) root="$a" ;;
  esac
  prev="$a"
done
put() {
  mkdir -p "$tree/$(dirname "$1")"
  printf '%s' "$2" >"$tree/$1"
  [ -z "$list" ] || printf '%s\n' "$1" >>"$list"
}
m=.ccnavi/approved/phases/i0001
case " $* " in
*" ops "* | *" host "*) exec '@EXE@' "$@" ;;
*" accept plan "* | *" accept threads "* | *" accept decided "*) exec '@EXE@' "$@" ;;
*" review prepare "*)
  mkdir -p "$root/logs/state"
  printf '<!-- ccnavi:request i0001:1 key=k1 -->\n見てほしい\n' >"$root/logs/state/req.md"
  printf 'タイトル\n\n本文\n' >"$root/logs/state/mr.md"
  printf '%s\n%s\n' "$root/logs/state/req.md" "$root/logs/state/mr.md"
  exit 0 ;;
@PLAN_CASE@*" review requested "*)
  if [ -n "${HALF_FAIL:-}" ] && [ ! -e "$HALF_FAIL" ]; then
    : >"$HALF_FAIL"; : >"$list"; exit 1
  fi
  : >"$list"; put "$m/1.requested" '{"mr": 1, "at": "t"}'; exit 0 ;;
*" review confirm "*)
  [ -n "$list" ] || exit 0
  : >"$list"; put "$m/1.reviewed" '{"mr": 1, "accepted": [], "at": "t"}'; exit 0 ;;
*" review ready "*)
  [ -n "$list" ] || exit 0
  : >"$list"; put "$m/ready.json" '{"mr": 1, "at": "t"}'; exit 0 ;;
*" user close-early "*" --accept-check "*) exit 0 ;;
*" user close-early "*) put "$m/2.skipped" '{"by": "close-early", "at": "t"}'; exit 0 ;;
esac
` + strings.ReplaceAll(userEntry, "--record-writes | --record-tree) skip=1", "--record-writes | --record-tree | --entry) skip=1") + `
if [ "$first" = user ]; then
  exec '@EXE@' "$@" --guard-ticket-approval disable
fi
exec '@EXE@' --guard-ticket-approval disable "$@"
`

// fill は @名前@ を差し込む。名前と値を交互に渡す。
func fill(text string, pairs ...string) string {
	for i := 0; i+1 < len(pairs); i += 2 {
		text = strings.ReplaceAll(text, "@"+pairs[i]+"@", pairs[i+1])
	}
	return text
}

// prelude は wapPrelude に本物の実行ファイルを差し込んだもの。
func (k *kit) prelude() string { return fill(wapPrelude, "EXE", k.exePath) }

// acceptPlan は --accept-plan のスタブの case（teststub と spec を差し込む）。
func (k *kit) acceptPlan() string {
	spec := write(k.t, filepath.Join(k.base, "accept-plan-spec.json"), acceptPlanSpec)
	return fill(acceptPlanCase, "STUB", slash(exe.TeststubPath(k.t)), "SPEC", slash(spec))
}

func slash(path string) string { return strings.ReplaceAll(path, `\`, "/") }

// installHalf は half の置き場に name の実行ファイルの代わり（half か hostHalf）を置き、パスを返す。
func (k *kit) installHalf(name, template string) string {
	return writeExec(k.t, filepath.Join(k.base, "bin", name),
		fill(template, "PRELUDE", k.prelude(), "PLAN_CASE", k.acceptPlan(), "EXE", k.exePath))
}

// wrapExe は実行ファイル bin の前に、body の sh を挟む実行ファイルの代わりを置く。body の後で "$@" を target に渡す。
func (k *kit) wrapExe(name, body, target string) string {
	return writeExec(k.t, filepath.Join(k.base, "bin", name), "#!/bin/sh\n"+body+"exec '"+target+"' \"$@\"\n")
}
