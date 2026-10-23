# ccnavi-common-review.sh  レビューの sh が共有する、ホストのマージリクエストを読む側の部品。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#   review_label=ccnavi-review   # 文面の頭
#   ccnavi_load ccnavi-common-review.sh   # ccnavi-common.sh の関数。無ければ配り直しを言って止まる
#
# 読むのは ccnavi-review.sh（エージェントも使う）と、ユーザ専用の ccnavi-user-decide.sh・
# ccnavi-user-close-early.sh。ccnavi-common.sh はこのファイルを自分では読まない（ホストの道具が要るので、レビューの sh だけが読む）。
# 読んだだけでは何もしない。関数と変数の既定値を決めるだけで、呼ぶ側が次の順に呼ぶ。
#
#   review_place            ワークスペースルート・いまのワークツリー・state の置き場（root・here・state・script_dir・exe_cwd）
#   review_exe              実行ファイル（関数 ccnavi）。見つからなければ 1
#   review_host             origin からホストと道具を決める（kind・host・path・JQ・transport など）。branch・family も
#   review_begin            受け付けたことを診断ログに残し、取ってきた結果の置き場（result）を決める
#   review_wap              write-and-push の支度（ccnavi_wap_*・wap_on）
#
# 置くのは、ホストを読む関数（api・pages・find_mr・threads・reviews・fetch_all・refetch・account）と、
# エージェントも使う書く関数（comment。ccnavi-review.sh の request・comment が投稿に使う）と、write-and-push を回す関数
# （wap_start・wap_ccnavi）だけ。ユーザの判断を書く処理（指摘のスレッドへの返信と解決、issue を作る、
# ユーザの判断を承認の push で送る）はここに置かない。ユーザ専用の sh（ccnavi-user-*.sh）の中だけに置く。
# 共通のファイルに置くと、`sh -c '. …common….sh; 関数'` の形で、ユーザ専用の sh の名前を出さずに
# 呼べてしまうため（hook はユーザ専用の sh を名前で止める）。
#
# リモートへの道具は、gh / glab があればそれ（認証はツールに任せる）、無ければ curl と
# GITHUB_TOKEN / GITLAB_TOKEN。どちらも無ければ止まる。結果の組み立てには jq が要る。
# 道具は起動時に絶対パスへ解いて固定する。PATH の細工で差し替えられないように。

# 文面の頭。読む前に呼ぶ側が決める。
review_label="${review_label:-ccnavi-review}"
# 診断ログに残すサブコマンドの名前。呼ぶ側が決める。
sub="${sub:-}"

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は止めた理由の種類を
# 表す短い語で、診断ログにだけ残す。文面には origin やパスが入るので、ログには写さない。
fail() {
	printf '%s: %s\n' "$review_label" "$2" >&2
	# 診断ログ。上の文面は契約として決まっている出力で、診断ログはそれとは別に残すだけ。
	log_info 止めた -- "sub=${sub:-}" "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
}

# ---- 場所。ワークスペースルートと、いまのワークツリー。Windows の Git Bash では pwd -W でパスの表記を直す。
#
# 根は git に聞かない。モード B では cwd がプロジェクトの中にあると git は
# プロジェクトを答え、チケット・マーカー・状態の置き場がプロジェクト側を指してしまう。
# 道具の置き場は上へたどって探す。
review_place() {
	root=$(ccnavi_workspace) ||
		fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
	# 解いたルートを logger に渡し、書くたびに探し直させない。
	ccnavi_log_root="$root"
	here="$(pwd -W 2>/dev/null || pwd)"
	state="$root/logs/state" # 固定
	# このスクリプトの置き場（絶対パス）。`ready --parent` は親のワークツリーへ cd するので、相対の `$0`
	# （`sh .ccnavi/scripts/ccnavi-review.sh`）をその後で解くと、親のワークツリーの中の写しを指すか、見失う。
	# cd の前に解いておき、以降はこれを使う。
	script_dir=$(cd "$(dirname "$0")" && pwd)
	# 実行ファイルに渡す cwd（どの親かを引く場所）。ふつうは打った場所で、`ready --parent <親>` のときだけ
	# 親のワークツリーにする。
	exe_cwd="$here"
}

# ---- 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。

# 実行ファイルと互換の版が食い違っていれば、最初に呼ぶときに 1 度だけ言う（止めない）。
# 引数の誤りで断る経路と、実行ファイルを使わないサブコマンド（origin / fetch / comment）では起こさない。
# `$( )` の中から呼ぶと、1 度言ったことの目印（told_skew）が親に残らないので、そう呼ぶ前には親で先に tell_skew を打つ。
told_skew=""
tell_skew() {
	[ -z "$told_skew" ] || return 0
	told_skew=1
	# ソースで動かすとき（bin が無い）は、sh と同じツリーのものを動かすので比べない。
	[ -n "${bin:-}" ] || return 0
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf '%s: %s\n' "$review_label" "$skew" >&2
}

# 見つからなければ 1（止めるかどうかは呼ぶ側が決める。merged は実行ファイルを起こさないので、無くても進める）。
review_exe() {
	if bin=$(ccnavi_bin "$root"); then
		ccnavi() {
			tell_skew
			"$bin" --root "$root" --cwd "$exe_cwd" "$@"
		}
		return 0
	fi
	bin=""
	if [ -f "$root/src/ccnavi/__main__.py" ]; then
		ccnavi() { (cd "$root" && uv run python -m ccnavi --root "$root" --cwd "$exe_cwd" "$@"); }
		return 0
	fi
	return 1
}

review_no_bin() {
	fail no-bin "ccnavi の実行ファイルが見つかりません（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。" 2
}

# 実行ファイルがそのフラグを知っているか（`--version --json` の flags で見る）。
# 知らない古い実行ファイルには `--actor`・`--via` を渡さない（渡すと引数の誤りで失敗する。マーカーは前と同じ中身になる）。
# 答えは 1 度だけ引いて覚えておく。`$( )` の中から呼ぶと覚えた値が親に残らないので、親で呼ぶ。
exe_flags=""
exe_flags_read=""
exe_knows() {
	if [ -z "$exe_flags_read" ]; then
		exe_flags_read=1
		exe_flags=$(ccnavi --version --json </dev/null 2>/dev/null | tr -d '\r') || exe_flags=""
	fi
	case "$exe_flags" in
	*"\"$1\""*) return 0 ;;
	esac
	return 1
}

# ---- リモート。origin の URL でホストを見分ける。
review_host() {
	origin=$(git remote get-url origin 2>/dev/null || :)
	[ -z "$origin" ] && fail no-origin "origin がありません。レビューは実際のマージリクエストに結び付けるので、リモートが必要です。"
	# 伏せた表記を、読む前に 1 度だけ作る。以降、文面に使うのはこれだけ。
	# 生の $origin を文面に入れる書き方を 1 つも残さないことで、次に fail を足す人が
	# 気づかずに生の値を使わないようにする。URL に資格情報を埋める使い方は普通にあり、
	# 出力はエージェントの文脈にも記録にも残る。
	origin_shown=$(ccnavi_mask_url "$origin")
	# origin の読み方と道具の選び方は ccnavi-common-host.sh の「ホスト（GitHub / GitLab）への接続」にある
	# （ccnavi-branches.sh と共有する）。ここは失敗の文面を決め、以降で使う変数に入れるだけ。
	ccnavi_host_parse "$origin" || {
		case "$?" in
		2) fail origin-no-host "origin からホストを読めない ($origin_shown)。" ;;
		3) fail origin-no-path "origin からプロジェクトのパスを読めない ($origin_shown)。" ;;
		4) fail origin-ghe-port "$ccnavi_h_parse_why ($origin_shown)。ポートを外した origin にしてください。" ;;
		*) fail origin-unreadable "origin の URL を読めない ($origin_shown)。" ;;
		esac
	}
	scheme="$ccnavi_h_scheme"
	host="$ccnavi_h_host"
	path="$ccnavi_h_path"
	kind="$ccnavi_h_kind"
	token_name="$ccnavi_h_token_name"
	api_base="$ccnavi_h_api_base"
	branch=$(git rev-parse --abbrev-ref HEAD)

	# ---- 道具。絶対パスに解いて固定する。
	ccnavi_host_connect || {
		case "$?" in
		3) fail no-jq "jqが見つかりません。結果の JSON を作成できません。" 2 ;;
		4) fail no-transport-token "$ccnavi_h_cli_name が $host で使えず（未導入か未認証）、curl に付ける $token_name もありません。$token_name を置くか、$ccnavi_h_cli_name を $host に認証してください。" 2 ;;
		*) fail no-transport "$ccnavi_h_cli_name が $host で使えず、curl もありません。どちらかを用意するか、スレッドとレビューの JSON を実行ファイルへ直接渡せるのはユーザだけなので、エージェントは JSON を作らずに止まり、ユーザに頼んでください（ユーザが MCP などでリモートを読める道具でスレッドとレビューを JSON にし、端末で 'ccnavi review confirm --phase <N> --result <json>' を実行します）。" 2 ;;
		esac
	}
	JQ="$ccnavi_h_jq"
	CLI="$ccnavi_h_cli"
	CURL="$ccnavi_h_curl"
	token="$ccnavi_h_token"
	transport="$ccnavi_h_transport"
	ccnavi_h_tmp="$state"
	ccnavi_h_on_fail=api_failed
	branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	# 親の識別子。親のワークツリー（.claude/worktrees/<識別子>）の名前で、親のブランチ名
	# の / を - に置き換えたもの（ブランチ名とは違うことがある）。マージリクエストは居るブランチ（branch）で探して作り、
	# write-and-push の親子のチケット・下書きの名前（実行ファイルと揃える）は識別子で引く。ワークツリーの外なら前どおりブランチ名。
	family="$branch"
	review_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	if [ -n "$review_top" ]; then
		review_top_p=$(ccnavi_phys "$review_top")
		review_root_p=$(ccnavi_phys "$root")
		case "$review_top_p" in
		"$review_root_p"/.claude/worktrees/*)
			family="${review_top_p#"$review_root_p"/.claude/worktrees/}"
			family="${family%%/*}"
			;;
		esac
	fi
}

# api <METHOD> <path> [<JSON body>] レスポンスの JSON を標準出力へ。path は api_base からの相対。
# 中身は ccnavi-common-host.sh の ccnavi_host_api。失敗したら標準出力には何も出さず、ホストが返した本文ごと
# 標準エラーへ出して（api_failed）1 を返す。curl の 1 回の時間の上限は ccnavi_h_max_time（既定 120 秒）。
api() {
	ccnavi_host_api "$@"
}

api_failed() {
	printf '%s: %s への %s %s が失敗した:\n%s\n' "$review_label" "$host" "$1" "$2" "$3" >&2
	# GraphQL を制限している実行環境がある（Claude Code のセッションは REST だけ通す）。
	# GitHub ではスレッドの解決状態（threads）と Draft 外し（undraft）が GraphQL でしか
	# 扱えないので、そこだけが 403 で止まる。curl の経路は -f が本文を捨てるため、
	# 画面に残るのは番号だけになり、認証の失敗と見分けが付かない。止まったその場で
	# 代わりの方法を名指しする。書き方は transport を選ぶところの案内と揃える。
	case "$2" in
	graphql)
		printf '%s: %s\n' "$review_label" \
			"GraphQL を使えない環境では confirm / fetch（スレッドの解決状態）と ready（Draft 外し）が通りません。スレッドとレビューの JSON を実行ファイルへ直接渡せるのはユーザだけです。エージェントは JSON を作らず、実行せずに止まり、ユーザに次を頼んでください: MCP などリモートを読める道具でスレッドとレビューを fetch_all と同じ形 {host, mr, threads, reviews, fetched_at} の JSON にし、端末で 'ccnavi review confirm --phase <N> --result <json>' を実行します。Draft 外しもユーザにその道具の側で行ってもらってください。" >&2
		;;
	esac
}

# pages <path> 100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページで打ち切って失敗。
pages() {
	pages_rc=0
	ccnavi_host_pages "$1" || pages_rc=$?
	[ "$pages_rc" -eq 2 ] && fail too-many-pages "$1 が多すぎて読み切れない。"
	return "$pages_rc"
}

encoded_path() {
	ccnavi_host_encoded_path
}

# ---- マージリクエスト。無ければ空。

find_mr() {
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		api GET "repos/$path/pulls?state=open&head=$(ccnavi_host_encode "$owner:$branch")" |
			"$JQ" '.[0] // empty | {number: .number, url: .html_url, source: .head.ref, target: .base.ref, draft: (.draft // false)}'
	else
		# このプロジェクトのブランチから出た MR だけ。
		# API は source_project_id で絞れないので、全ページを読んでから絞る（1 ページ目がフォークで埋まっても目的のものを外さない）
		pid=$(project_id) || fail no-project-id "GitLab のプロジェクト $path の id を読めない。マージリクエストがこのプロジェクトから出たかを確かめられないので止めた（PAT の権限と origin の URL を見直してください）。"
		mrs=$(pages "projects/$(encoded_path)/merge_requests?state=opened&source_branch=$(ccnavi_host_encode "$branch")") ||
			fail mr-list-failed "親ブランチ $branch のマージリクエストの一覧を読めない（ホストの返事は上に出ている）。"
		printf '%s' "$mrs" |
			"$JQ" --argjson pid "$pid" '[.[] | select(.source_project_id == $pid)][0] // empty | {number: .iid, url: .web_url, source: .source_branch, target: .target_branch, draft: (.draft // .work_in_progress // false)}'
	fi
}

# GitLab のプロジェクトの数の id。読めなければ 1
project_id() {
	ccnavi_host_project_id
}

# ---- スレッド。GitHub は GraphQL でしか解決状態を読めない。GitLab は discussions。

threads() {
	mr_number="$1"
	mr_url="$2"
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		repo="${path#*/}"
		cursor=null
		all='[]'
		page=0
		while :; do
			query=$("$JQ" -n --arg o "$owner" --arg r "$repo" --argjson n "$mr_number" --argjson c "$cursor" '{
				query: "query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor} nodes{id isResolved comments(first:1){nodes{url path line body createdAt}} last:comments(last:1){nodes{id createdAt author{login}}}}}}}}",
				variables: {o: $o, r: $r, n: $n, c: $c}}')
			res=$(api POST graphql "$query")
			chunk=$(printf '%s' "$res" | "$JQ" '[.data.repository.pullRequest.reviewThreads.nodes[] | . as $t | (.comments.nodes[0] // {}) as $c | (.last.nodes[0] // {}) as $l |
				{id: $t.id, resolved: $t.isResolved, url: ($c.url // ""), path: ($c.path // ""), line: ($c.line // 0), body: ($c.body // ""), created_at: ($c.createdAt // ""),
				 last_id: ($l.id // ""), last_at: ($l.createdAt // ""), last_author: ($l.author.login // "")}]')
			all=$(printf '%s\n%s' "$all" "$chunk" | "$JQ" -s '.[0] + .[1]')
			more=$(printf '%s' "$res" | "$JQ" -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
			[ "$more" = true ] || break
			cursor=$(printf '%s' "$res" | "$JQ" '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
			page=$((page + 1))
			[ "$page" -gt 20 ] && fail too-many-threads "reviewThreads が多すぎて読み切れない。"
		done
		printf '%s' "$all"
	else
		# 判定に使う note は、discussion の note から system: true を除いたもの（空なら discussion ごと除く）。
		# 対象は、その note が 1 つでも resolvable な discussion。代表の note（本文・url・位置・作成者）は
		# 本文が目印 `<!-- ccnavi:` で始まらない最初の note（無ければ先頭）。ccnavi の依頼コメントに
		# ユーザが返信したスレッドは、返信が代表になる。own は、note の本文が全部目印で始まるときだけ真
		# （ccnavi の投稿だけのスレッド）。last_* は集合の最後の note。
		pages "projects/$(encoded_path)/merge_requests/$mr_number/discussions" |
			"$JQ" --arg u "$mr_url" 'def marked: ((.body // "") | if type == "string" then startswith("<!-- ccnavi:") else false end);
				[.[] | . as $d | [($d.notes // [])[] | select(.system != true)] as $ns
				| select(($ns | length) > 0) | select($ns | any(.resolvable))
				| ([$ns[] | select(marked | not)] | .[0] // $ns[0]) as $n | $ns[-1] as $l |
				{id: ($d.id | tostring), resolved: ([$ns[] | select(.resolvable) | .resolved] | all),
				 url: ($u + "#note_" + ($n.id | tostring)), path: ($n.position.new_path // ""), line: ($n.position.new_line // 0),
				 body: ($n.body // ""), created_at: ($n.created_at // ""), author: (($n.author.id // "") | tostring),
				 own: ($ns | all(marked)),
				 last_id: (($l.id // "") | tostring), last_at: ($l.created_at // ""), last_author: ($l.author.username // "")}]'
	fi
}

# ---- レビュー。変更要求の状態を CHANGES_REQUESTED にそろえる。

reviews() {
	mr_number="$1"
	mr_url="$2"
	if [ "$kind" = github ]; then
		pages "repos/$path/pulls/$mr_number/reviews" |
			"$JQ" '[.[] | {state: (.state // ""), url: (.html_url // ""), submitted_at: (.submitted_at // ""), author: ((.user.id // .user.login // "") | tostring)}]'
	else
		pages "projects/$(encoded_path)/merge_requests/$mr_number/reviewers" |
			"$JQ" --arg u "$mr_url" '[.[] | {state: (if .state == "requested_changes" then "CHANGES_REQUESTED" else ((.state // "") | ascii_upcase) end),
				url: $u, submitted_at: (.updated_at // .created_at // ""), author: ((.user.id // .user.username // "") | tostring)}]'
	fi
}

# ---- 投稿。{url, created_at} を返す。

comment() {
	mr_number="$1"
	mr_url="$2"
	body_json=$("$JQ" -Rs '{body: .}' <"$3")
	if [ "$kind" = github ]; then
		# 応答に URL（GitLab は id）が無ければ、投稿できたと読める出力をせず、jq の非 0 で終える
		# （パイプの終了コードは最後の jq のもの）。呼び出し元は、終了コードでも .url の有無でも失敗と読む。
		api POST "repos/$path/issues/$mr_number/comments" "$body_json" |
			"$JQ" 'if ((.html_url // "") == "") then error("no html_url") else {url: .html_url, created_at: (.created_at // ""), author: (.user.login // "")} end'
	else
		api POST "projects/$(encoded_path)/merge_requests/$mr_number/notes" "$body_json" |
			"$JQ" --arg u "$mr_url" 'if .id == null then error("no note id") else {url: ($u + "#note_" + (.id | tostring)), created_at: (.created_at // ""), author: ((.author.id // "") | tostring)} end'
	fi
}

# ---- 目印で自分の投稿を探す（送ったかどうかの照合）。
#
# <種類 issue|note|issue-note> <操作ID> <段階ID> <対象の番号> <アカウント> <対象の URL>
# 種類: issue（issue の作成）、note（MR へのコメント）、issue-note（issue へのコメント）。
# 対象は note なら MR、issue-note なら issue（GitLab の URL を組むのに使う）。
# 標準出力は 1 行: `<yes|no|error> <URL,URL,…>`。1 つめは一覧を上限で打ち切らずに最後まで読めたか
# （yes）、打ち切ったか（no）、読めなかったか（error）。2 つめは目印が一致した投稿の URL。
# 目印は本文の末尾の `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->`。アカウントが分かれば、その人の投稿だけ数える
# （GitHub は `.user.login`、GitLab は `.author.username`）。検索 API は使わず、そのリポジトリの
# issue / MR のコメントの一覧を、新しい順に読む。
find_marked() {
	fm_kind="$1"
	fm_mark="<!-- ccnavi:op=$2 stage=$3 -->"
	fm_mr="$4"
	fm_who="$5"
	fm_url="${6:-}"
	fm_rc=0
	if [ "$kind" = github ]; then
		if [ "$fm_kind" = issue ]; then
			fm_rel="repos/$path/issues?state=all&sort=created&direction=desc"
			[ -z "$fm_who" ] || fm_rel="$fm_rel&creator=$(ccnavi_host_encode "$fm_who")"
		else
			fm_rel="repos/$path/issues/$fm_mr/comments?sort=created&direction=desc"
		fi
		fm_list=$(ccnavi_hg_partial=1 ccnavi_host_pages "$fm_rel") || fm_rc=$?
		fm_urls=$(printf '%s' "$fm_list" | "$JQ" -r --arg m "$fm_mark" --arg w "$fm_who" \
			'[.[]? | select(has("pull_request") | not) | select((.body // "") | contains($m)) | select($w == "" or ((.user.login // "") == $w)) | .html_url] | join(",")' 2>/dev/null) || fm_rc=1
	else
		if [ "$fm_kind" = issue ]; then
			fm_rel="projects/$(encoded_path)/issues?scope=all&order_by=created_at&sort=desc"
			[ -z "$fm_who" ] || fm_rel="$fm_rel&author_username=$(ccnavi_host_encode "$fm_who")"
		elif [ "$fm_kind" = issue-note ]; then
			fm_rel="projects/$(encoded_path)/issues/$fm_mr/notes?order_by=created_at&sort=desc"
		else
			fm_rel="projects/$(encoded_path)/merge_requests/$fm_mr/notes?order_by=created_at&sort=desc"
		fi
		fm_list=$(ccnavi_hg_partial=1 ccnavi_host_pages "$fm_rel") || fm_rc=$?
		fm_urls=$(printf '%s' "$fm_list" | "$JQ" -r --arg m "$fm_mark" --arg w "$fm_who" --arg u "${fm_url:-}" \
			'[.[]? | select(((.description // "") + (.body // "")) | contains($m)) | select($w == "" or ((.author.username // "") == $w)) | (.web_url // ($u + "#note_" + (.id | tostring)))] | join(",")' 2>/dev/null) || fm_rc=1
	fi
	case "$fm_rc" in
	0) printf 'yes %s\n' "$fm_urls" ;;
	2) printf 'no %s\n' "$fm_urls" ;;
	*) printf 'error \n' ;;
	esac
}

# ---- エージェントの単発の投稿（request・comment・ready の補助コメント、close-early の issue とコメント）を、
# 操作記録に載せて送る。
#
# staged_send <操作の種類> <鍵> <段階ID> <note|issue> <MR の番号> <MR の URL> <本文のファイル>
# 操作ID は種類・親・MR・鍵から決まる（打ち直しが同じ記録に当たる）。段階は issue と comment の 2 つを
# 記録に持つ。note は MR へのコメント、issue は issue の作成（create_issue が要る。本文の 1 行目が題）。
# 標準出力は note なら `comment`、issue なら `create_issue` と同じ形の JSON（送り済みと分かったときは
# {url}、issue はそれに {number}）。送り済みなら送らず、前に試した形跡があれば先にホストを目印で照合する。
# 確定できないときは止めて 1（自動では送り直さない）。
# 記録を作れない（古い実行ファイル・書けない）ときは、前と同じ送り方に戻る。

cs_set() {
	ccnavi ops stage-set "$cs_id" "$cs_stage" "$@" 2>/dev/null || {
		printf '%s: 操作記録に段階 %s を書けなかった。\n' "${review_label:-ccnavi-review}" "$cs_stage" >&2
		return 1
	}
}

cs_field() {
	ccnavi ops show "$cs_id" 2>/dev/null | "$JQ" -r --arg s "$cs_stage" --arg f "$1" '[.stages[]? | select(.id == $s)][0][$f] // empty'
}

# 送った先の URL から、返す JSON を作る。
cs_sent_json() {
	if [ "$cs_skind" = issue ]; then
		"$JQ" -n --arg u "$1" '{url: $u, number: ($u | split("/") | last | tonumber? // 0)}'
	else
		"$JQ" -n --arg u "$1" '{url: $u}'
	fi
}

staged_send() {
	cs_kind="$1"
	cs_key="$2"
	cs_stage="$3"
	cs_skind="$4"
	cs_mr="$5"
	cs_url="$6"
	cs_file="$7"
	cs_id=$(ccnavi ops begin "$cs_kind" "${branch:-}" 0 "$cs_mr" "$cs_key" "$cs_stage:$cs_stage" 2>/dev/null) || cs_id=""
	if [ -z "$cs_id" ]; then
		if [ "$cs_skind" = issue ]; then create_issue "$cs_file"; else comment "$cs_mr" "$cs_url" "$cs_file"; fi
		return $?
	fi
	# 同じ操作を同時に送らない（照合の前に 2 つが「未送信」と見て、両方が投稿するのを防ぐ）。
	# ロックが使えない構成（root が無い）では、取らずに進む。
	cs_locked=""
	if [ -n "${root:-}" ] && command -v ccnavi_lock_take >/dev/null 2>&1; then
		# 取る関数の状態はグローバル変数 1 組。write-and-push のロックを持ったまま取ると、その情報が
		# 上書きされて外せなくなるので、取る前に退避して、外したあとに戻す。
		cs_sv_dir="$ccnavi_lock_dir"
		cs_sv_mark="$ccnavi_lock_mark"
		cs_sv_set="$ccnavi_lock_set_held"
		cs_sv_prev="$ccnavi_lock_prev_held"
		cs_sv_prev_set="$ccnavi_lock_prev_set"
		if ccnavi_lock_take "$root" ops "$cs_id" 30; then
			cs_locked=1
		else
			ccnavi_lock_dir="$cs_sv_dir"
			ccnavi_lock_mark="$cs_sv_mark"
			printf '%s: 同じ投稿が別のプロセスで実行中。待っても取れなかったので止めた。\n' "${review_label:-ccnavi-review}" >&2
			return 1
		fi
	fi
	staged_send_locked
	cs_rc=$?
	if [ -n "$cs_locked" ]; then
		ccnavi_lock_drop
		ccnavi_lock_dir="$cs_sv_dir"
		ccnavi_lock_mark="$cs_sv_mark"
		ccnavi_lock_set_held="$cs_sv_set"
		ccnavi_lock_prev_held="$cs_sv_prev"
		ccnavi_lock_prev_set="$cs_sv_prev_set"
	fi
	return "$cs_rc"
}

# staged_send の本体（操作 ID のロックを持った状態で呼ぶ）。
staged_send_locked() {
	# 記録を読めないまま送ると、送り済みの段階をもう一度送りかねない。読めなければ止める。
	if ! ccnavi ops show "$cs_id" >/dev/null 2>&1; then
		printf '%s: 操作記録 %s を読めない。二重に送らないよう止めた。\n' "${review_label:-ccnavi-review}" "$cs_id" >&2
		return 1
	fi
	cs_state=$(cs_field state)
	if [ "$cs_state" = sent ]; then
		printf '%s: 同じ投稿は送信済み（%s）。送り直さない\n' "${review_label:-ccnavi-review}" "$(cs_field url)" >&2
		cs_sent_json "$(cs_field url)"
		return 0
	fi
	cs_who=""
	cs_tries=$(cs_field tries)
	if [ "${cs_tries:-0}" -gt 0 ] || [ "$cs_state" = unknown ]; then
		cs_who=$(account || :)
		cs_found=$(find_marked "$cs_skind" "$cs_id" "$cs_stage" "$cs_mr" "$cs_who" "$cs_url")
		cs_urls="${cs_found#* }"
		cs_verdict=$(ccnavi ops reconcile "$cs_id" "$cs_stage" "$cs_urls" "${cs_found%% *}" "$cs_who" 2>/dev/null) || cs_verdict="stop 照合できなかった"
		case "$cs_verdict" in
		sent*)
			cs_set sent "$cs_urls" "$cs_who" || :
			cs_sent_json "$cs_urls"
			return 0
			;;
		send*) : ;;
		*)
			cs_set unknown || :
			printf '%s: 投稿を送ったか確定できない（%s）。自動では送り直さない。ホストで目印 ccnavi:op=%s stage=%s を探して確かめてください。\n' \
				"${review_label:-ccnavi-review}" "${cs_verdict#* }" "$cs_id" "$cs_stage" >&2
			return 1
			;;
		esac
	fi
	cs_body="$state/review-staged-$$.md"
	{
		cat "$cs_file"
		printf '\n<!-- ccnavi:op=%s stage=%s -->\n' "$cs_id" "$cs_stage"
	} >"$cs_body"
	# 送る前の記録（試した回数と送るアカウント）を残せなければ、送らない（残らないと、落ちたあとの照合ができない）。
	[ -n "$cs_who" ] || cs_who=$(account || :)
	if ! cs_set pending "" "$cs_who"; then
		rm -f "$cs_body"
		return 1
	fi
	if [ "$cs_skind" = issue ]; then
		cs_out=$(create_issue "$cs_body") || cs_out=""
	else
		cs_out=$(comment "$cs_mr" "$cs_url" "$cs_body") || cs_out=""
	fi
	rm -f "$cs_body"
	cs_posted=$(printf '%s' "$cs_out" | "$JQ" -r '.url // empty' 2>/dev/null || :)
	if [ -z "$cs_posted" ]; then
		cs_set unknown || :
		return 1
	fi
	[ -n "$cs_who" ] || cs_who=$(account || :)
	cs_set sent "$cs_posted" "$cs_who" || :
	printf '%s\n' "$cs_out"
}

# MR へのコメント 1 つを送る（staged_send の note）。<操作の種類> <鍵> <MR の番号> <MR の URL> <本文のファイル>
comment_staged() {
	staged_send "$1" "$2" comment note "$3" "$4" "$5"
}

# 取ってきた時点の状態を取り直して $result に置く。
refetch() {
	fetch_all >"$result.re"
	mv -f "$result.re" "$result"
}

# ---- トークンの持ち主（レビュー済みマーカーの actor。記録に使う）。引けなければ空。
#
# gh / glab はその道具が認証したアカウント、curl はトークンの持ち主。GitHub は GET /user の login、
# GitLab は username。GitHub Actions の GITHUB_TOKEN のように持ち主の無いトークンは 403 で空になる。
# 形（英数字と _ . - の 1〜100 字で、- で始まらない。実行ファイルの --actor と同じ）に合わなければ使わない。
# マーカーに残すだけなので、長く待たない（account_max_time 秒）。gh / glab の標準エラーは応答に混ぜない。

account_max_time="${CCNAVI_REVIEW_ACCOUNT_TIMEOUT:-15}"

# timed <秒> <書き先> <コマンド>... 標準出力を書き先へ、標準エラーは捨てる。時間を過ぎたら止めて 1。
timed() {
	timed_limit="$1"
	timed_out="$2"
	shift 2
	"$@" </dev/null >"$timed_out" 2>/dev/null &
	timed_pid=$!
	# sleep は背景で起こして wait で待つ。TERM を受けたら sleep も止めて抜ける（sleep を ppid 1 で残さない）。
	(
		exec </dev/null >/dev/null 2>&1
		timed_sleep=""
		timed_stop=""
		# trap は印を立てて、分かっていれば sleep を止めるだけ。抜けるのは本流で、sleep の pid を控えた後に印を見る
		# （sleep を起こした直後に TERM が来ても、sleep を残さない）。
		trap 'timed_stop=1; test -z "$timed_sleep" || kill "$timed_sleep"' TERM
		sleep "$timed_limit" &
		timed_sleep=$!
		test -z "$timed_stop" || { kill "$timed_sleep"; exit 0; }
		wait "$timed_sleep"
		test -z "$timed_stop" || exit 0
		kill "$timed_pid"
	) &
	timed_dog=$!
	timed_rc=0
	wait "$timed_pid" 2>/dev/null || timed_rc=$?
	kill "$timed_dog" 2>/dev/null || :
	# 見張りの終了を回収して、シェルに「Terminated」の報告を出させない。
	wait "$timed_dog" 2>/dev/null || :
	return "$timed_rc"
}

account() {
	if [ "$kind" = github ]; then field=login; else field=username; fi
	account_out="$state/review-account-$$.json"
	case "$transport" in
	gh | glab) timed "$account_max_time" "$account_out" "$CLI" api --hostname "$host" user || : >"$account_out" ;;
	curl)
		ccnavi_h_max_time="$account_max_time"
		api GET user >"$account_out" 2>/dev/null || : >"$account_out"
		ccnavi_h_max_time=120
		;;
	esac
	who=$("$JQ" -r --arg f "$field" '.[$f] // empty' <"$account_out" 2>/dev/null | tr -d '\r') || who=""
	rm -f "$account_out"
	[ -n "$who" ] || return 0
	# 形は C ロケールで見る（ロケールによって [A-Za-z] が英字の外に当たらないように）
	if printf '%s\n' "$who" | LC_ALL=C grep -q '[^A-Za-z0-9_.-]'; then return 0; fi
	case "$who" in
	-*) return 0 ;;
	esac
	[ "${#who}" -le 100 ] || return 0
	printf '%s' "$who"
}

# ---- 取ってきた時点の状態。exe に渡す JSON。

fetch_all() {
	mr=$(find_mr)
	[ -z "$mr" ] && fail no-mr "親ブランチ $branch に対応するマージリクエストが $host にありません。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	# Draft のままか。`ready` の打ち直しの判断にだけ使うので、結果の JSON には入れず変数に覚える
	# （`ready` が結果に足す。Chrome が組む結果と形を揃えたまま）。
	mr_draft=$(printf '%s' "$mr" | "$JQ" -r '.draft // false')
	# 実行ファイルに渡す形は番号と url だけ（元ブランチとマージ先は依頼のときだけ sh が確かめる）
	mr=$(printf '%s' "$mr" | "$JQ" -c '{number: .number, url: .url}')
	t=$(threads "$number" "$url")
	r=$(reviews "$number" "$url")
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson threads "$t" --argjson reviews "$r" \
		'{host: $host, mr: $mr, threads: $threads, reviews: $reviews, fetched_at: (now | todate)}'
}

# ---- 受け付けたことを診断ログに残し、取ってきた結果（実行ファイルに渡す JSON）の置き場を決める。
# 抜けるときの後始末（trap）は呼ぶ側が置く（消すものが sh ごとに違う）。
review_begin() {
	log_info 受け付けた -- "sub=$sub" "args=$1"
	log_debug 判定の材料 -- "sub=$sub" "kind=${kind:-}" "host=${host:-}" "branch=$branch" "transport=${transport:-}" "bin=${bin:-}"
	mkdir -p "$state"
	result="$state/review-result-$$.json"
}

# ---- write-and-push。取り込み済みの親子のチケットで、状態を書く実行ファイルの呼び出しを 1 つの操作にまとめる。
wap_on=no
review_wap() {
	ccnavi_wap_root="$root"
	ccnavi_wap_sh="$script_dir"
	# 実行ファイルの起こし方は review_exe が決めた ccnavi（--cwd と互換の版の確かめつき）。
	ccnavi_wap_exe() { ccnavi "$@"; }
	ccnavi_wap_label="$review_label"
}

# write-and-push を始める（1〜5）。<識別子>。対象でなければ何もしない（今の手元の動き）。止める理由があれば抜ける。
wap_start() {
	ccnavi_wap_family "$1"
	case "$ccnavi_wap_target" in
	stop)
		ccnavi_wap_refuse
		exit 1
		;;
	yes)
		ccnavi_wap_begin || exit 1
		wap_on=yes
		;;
	esac
	return 0
}

# 状態を書く実行ファイルの 1 回。write-and-push の中なら書いたパスだけをコミットして送る（6〜11）。
# <文> -- <実行ファイルの引数>...。ccnavi_wap_capture に書き先があれば標準出力をそこへ書く。
wap_ccnavi() {
	wap_words="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	if [ "$wap_on" = yes ]; then
		ccnavi_wap_write "ccnavi: ${wap_words}" -- "$@"
	elif [ -n "$ccnavi_wap_capture" ]; then
		ccnavi "$@" >"$ccnavi_wap_capture"
	else
		ccnavi "$@"
	fi
}
