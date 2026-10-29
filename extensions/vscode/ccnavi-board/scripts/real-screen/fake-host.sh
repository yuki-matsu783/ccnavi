#!/bin/sh
# 偽の GitLab（tests/manual/gitlab/fake_gitlab.py）と手元の bare を立て、検証用コピーの origin をそこへ向ける。
# チケット作業は issue と MR（ホスト）が要るので、実画面の確認と haiku の通し試験の前に使う。
#
#   sh fake-host.sh up   <作業ディレクトリ> <検証用コピー> [<ポート>] [<issue のタイトル> [<issue の本文>]]
#   sh fake-host.sh down <作業ディレクトリ>
#
# up のあと、チケット作業を動かすシェルで次を設定する（出力の最後にも出す）。
#   export PATH=<作業ディレクトリ>/bin:$PATH GITLAB_TOKEN=fake-gitlab-token-0001
# bin/git は `git remote get-url origin` だけホストの URL を答え、あとは本物の git へ渡す（fetch・push は bare へ向く）。
# 検証用コピーは、履歴を作り直して（shallow を避ける）main を bare へ push する。コピー元には触らない。
set -eu

here=$(cd "$(dirname "$0")" && pwd)
ccnavi=$(cd "$here/../../../../.." && pwd)
cmd=${1:?up か down}
work=${2:?作業ディレクトリ}

case "$cmd" in
up)
	repo=${3:?検証用コピー}
	port=${4:-8929}
	title=${5:-"README に使い方の実行例を足す"}
	body=${6:-"README.md の「使い方」に、実際に動かした例（入力と出力）を 1 つ足してください。"}
	mkdir -p "$work/bin"
	rm -rf "$work/remote.git"
	git init -q --bare -b main "$work/remote.git"
	TITLE=$title BODY=$body PORT=$port python3 - "$work/state.json" <<'PY'
import json, os, sys
issue = {"iid": 1, "title": os.environ["TITLE"], "description": os.environ["BODY"], "state": "opened", "labels": [],
         "web_url": f"http://127.0.0.1:{os.environ['PORT']}/demo/greeter/-/issues/1", "author": {"id": 2, "username": "reviewer"}}
json.dump({"project": "demo/greeter", "seq": 100, "mrs": [], "issues": [issue]}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
	(cd "$ccnavi" && setsid nohup uv run python tests/manual/gitlab/fake_gitlab.py "$work/state.json" "$port" >"$work/fake.log" 2>&1 </dev/null &)
	i=0
	until grep -q listening "$work/fake.log" 2>/dev/null; do
		i=$((i + 1))
		[ "$i" -le 30 ] || { echo "偽のホストが起動しない: $work/fake.log" >&2; exit 1; }
		sleep 1
	done
	cat >"$work/bin/git" <<EOF
#!/bin/sh
self=\$(dirname "\$0")
rest=""; oldifs=\$IFS; IFS=:
for p in \$PATH; do [ "\$p" = "\$self" ] || rest="\$rest:\$p"; done
IFS=\$oldifs
case " \$* " in
*" remote get-url origin "*) echo http://127.0.0.1:$port/demo/greeter.git; exit 0 ;;
esac
PATH="\${rest#:}" exec git "\$@"
EOF
	chmod +x "$work/bin/git"
	rm -rf "$repo/.git"
	git -C "$repo" init -q -b main
	git -C "$repo" config user.name tester
	git -C "$repo" config user.email t@t
	git -C "$repo" add -A
	git -C "$repo" commit -qm "初期状態"
	git -C "$repo" remote add origin "$work/remote.git"
	git -C "$repo" push -q -u origin main
	git -C "$repo" remote set-head origin -a >/dev/null
	echo "起動した: http://127.0.0.1:${port}（状態 ${work}/state.json）"
	echo "次を設定する: export PATH=$work/bin:\$PATH GITLAB_TOKEN=fake-gitlab-token-0001"
	;;
down)
	pid=$(pgrep -f "[f]ake_gitlab.py $work/state.json" || :)
	[ -z "$pid" ] || kill $pid
	echo "止めた"
	;;
*)
	echo "使い方: sh fake-host.sh up|down <作業ディレクトリ> ..." >&2
	exit 2
	;;
esac
