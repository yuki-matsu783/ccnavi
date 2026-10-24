#!/bin/sh
# ccnavi-install は、ccnavi を入れる入口。状況を調べ（ccnavi-doctor.sh）、入れる先に配り（ccnavi-setup.sh）、
# 最後に lint で確かめる。ccnavi-setup.sh の中身は変えず、呼ぶだけ。
#
#   sh <配布元>/scripts/ccnavi-install.sh <ワークスペースルート> [--from <配布元>]
#        [--mode dry-run|enable] [--ticket-control enable|disable] [--force] [--no-vscode] [--no-fetch] [--no-idle]
#        [--clear-quarantine] [--check]
#   sh <ソース>/scripts/ccnavi-install.sh <ワークスペースルート> --build [--source <ソース>]
#        [--with-extensions] [--check]
#
#   --from <配布元>        展開済みの配布元のフォルダ。省くと、このスクリプトの 1 つ上。zip は受けない
#   --mode / --ticket-control / --force / --no-vscode / --no-fetch / --no-idle
#                          ccnavi-setup.sh にそのまま渡す（ccnavi-setup.sh --help）
#                          --bin は渡さない。ホームディレクトリに書き込む確認をターミナルに出すので、ccnavi-setup.sh を直接実行する
#   --clear-quarantine     macOS で、入れた実行ファイルの隔離の印（com.apple.quarantine）を外す
#   --check                何も書かずに、これから起きることを見せる
#   --build                ソースで実行ファイルを組み立てる。配らない。組み立てたら、そのソースを
#                          --from に渡した --check へ進む
#   --source <ソース>      --build のソース。省くと、このスクリプトの 1 つ上
#   --with-extensions      --build で、拡張機能も組み、VS Code に入れる（ccnavi-build-install.sh）
#
# 終了コード: 0 成功 / 1 --check で揃っていない / 2 引数の誤り / 3 前提が足りない・入れてはいけない状態 /
#   4 途中で失敗（入れる先は書き換わっていないか、一部だけ）/ 5 入れたが、lint に error がある
#
# 配布元（--from のフォルダ）は、どの経路でも変更しない（chmod も xattr も書き込みもしない）。
# 例外は --build で、ソースの pull と組み立てだけを行う。
# 出力全体で「次:」は 1 行。子の出力（doctor・setup・lint）にある「次:」は「案内:」に読み替える。

set -eu

install_next_done=no
install_tmp=""
install_argline=""

usage() {
	printf '%s\n' \
		'sh <配布元>/scripts/ccnavi-install.sh <ワークスペースルート> [--from <配布元>]' \
		'  [--mode dry-run|enable] [--ticket-control enable|disable] [--force] [--no-vscode] [--no-fetch] [--no-idle]' \
		'  [--clear-quarantine] [--check]' \
		'sh <ソース>/scripts/ccnavi-install.sh <ワークスペースルート> --build [--source <ソース>]' \
		'  [--with-extensions] [--check]' \
		'' \
		'zip は受けません。展開してから、展開したフォルダを --from に渡します。' \
		'終了コード: 0 成功 / 1 --check で揃っていない / 2 引数の誤り / 3 前提が足りない・入れてはいけない状態 /' \
		'  4 途中で失敗 / 5 入れたが lint に error がある'
}

die() {
	printf 'ccnavi-install: %s\n' "$1" >&2
	exit "${2:-2}"
}

cleanup() {
	[ -z "$install_tmp" ] || rm -rf "$install_tmp"
	return 0
}

# 引数を単引用符で囲んで出す（ccnavi_sh_quote と同じ形）。
iq() {
	iq_rest=$1
	iq_out=""
	while :; do
		case "$iq_rest" in
		*\'*)
			iq_out="${iq_out}${iq_rest%%\'*}'\"'\"'"
			iq_rest=${iq_rest#*\'}
			;;
		*) break ;;
		esac
	done
	printf "'%s%s'" "$iq_out" "$iq_rest"
}

# 絶対パスにする。Git Bash では Windows 形式（pwd -W）にそろえる。
iabs() {
	(cd -- "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })
}

# リンクを解いた物理パス。同じ場所かどうかの比較専用（pwd は論理パスで、リンク経由だと別の綴りになる）。
iphys() {
	(cd -- "$1" 2>/dev/null && pwd -P)
}

# 大文字小文字と区切りを区別せずに比べるための形（比べる専用。開くときは元の表記を使う）。
ifold() {
	printf '%s' "$1" | tr 'A-Z\\' 'a-z/'
}

# 「次:」は出力全体で 1 行。2 回目以降は案内に落とす。
inext() {
	if [ "$install_next_done" = yes ]; then
		printf '案内: %s\n' "$1"
		return 0
	fi
	install_next_done=yes
	printf '次: %s\n' "$1"
}

# 子の出力を出す。「次:」は「案内:」に読み替える。<標準出力のファイル> <標準エラーのファイル>
ishow() {
	if [ -s "$1" ]; then
		sed 's/^次: /案内: /' "$1"
	fi
	if [ -s "$2" ]; then
		sed 's/^次: /案内: /' "$2" >&2
	fi
	return 0
}

# 設定ファイルの env.CCNAVI_MODE（保存された値）。.claude/settings.local.json を上に重ねる。
# 標準出力: 値（無ければ空）。戻り値: 0 / 3 JSON として読めないファイルがある（標準出力にそのファイル名）
istored_mode() {
	sm_value=""
	for sm_rel in .claude/settings.json .claude/settings.local.json; do
		[ -f "$1/$sm_rel" ] || continue
		if ! jq empty "$1/$sm_rel" >/dev/null 2>&1; then
			printf '%s' "$sm_rel"
			return 3
		fi
		sm_got=$(jq -r 'if type == "object" and (.env | type) == "object" and (.env.CCNAVI_MODE | type) == "string" then "set:" + .env.CCNAVI_MODE else "unset" end' "$1/$sm_rel" | tr -d '\r' | sed -n 1p)
		case "$sm_got" in
		set:*) sm_value=${sm_got#set:} ;;
		esac
	done
	printf '%s' "$sm_value" | tr 'A-Z' 'a-z' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# 配布元を決める。<起点> → 標準出力に配布元。戻り値: 0 / 3 決められない（標準出力に理由）
# scripts/ccnavi-setup.sh が直下にあればそこ。直下に無く、1 段下にちょうど 1 つあればそこ。2 つ以上は決められない。
iresolve_base() {
	if [ -f "$1/scripts/ccnavi-setup.sh" ]; then
		iabs "$1"
		return 0
	fi
	rb_count=0
	rb_hit=""
	for rb_dir in "$1"/*/; do
		[ -f "${rb_dir}scripts/ccnavi-setup.sh" ] || continue
		rb_count=$((rb_count + 1))
		rb_hit=$rb_dir
	done
	case "$rb_count" in
	1)
		iabs "$rb_hit"
		return 0
		;;
	0) printf '%s に scripts/ccnavi-setup.sh が見つからない（直下にも 1 段下にも無い）' "$1" ;;
	*) printf '%s の 1 段下に scripts/ccnavi-setup.sh を持つフォルダが %s つあり、どれを使うか決められない' "$1" "$rb_count" ;;
	esac
	return 3
}

# doctor の出力から todo の項目を案内に並べ直す。<doctor の出力のファイル>
itodos() {
	sed -n -e 's/^check: \([^ ]*\) todo \([^ ]*\) \(.*\)$/案内: \1 が todo（\2）: \3/p' \
		-e 's/^check: \([^ ]*\) todo \([^ ]*\)$/案内: \1 が todo（\2）/p' "$1"
}

# --force のバックアップ。入れる先の外に、権限 700 で作る。置き場を 1 行の標準出力に出す。
# <ws> <配布元>
ibackup() {
	bk_cache=${XDG_CACHE_HOME:-${HOME:-}/.cache}
	# 絶対パスだけ受ける。相対パスは、入れる先の中を指すことがあり、HOME が空なら /.cache になる。
	case "$bk_cache" in
	/* | [A-Za-z]:[/\\]*) ;;
	*) return 1 ;;
	esac
	[ -n "${XDG_CACHE_HOME:-}" ] || [ -n "${HOME:-}" ] || return 1
	bk_hash=$(printf '%s' "$(ifold "$1")" | cksum | sed 's/ .*//')
	bk_stamp=$(date +%Y%m%d-%H%M%S)
	bk_dir="$bk_cache/ccnavi/backup/$bk_hash-$bk_stamp-$$"
	bk_abs_fold=$(ifold "$bk_dir")
	case "$bk_abs_fold" in
	"$(ifold "$1")"/*) return 1 ;;
	esac
	(
		umask 077
		mkdir -p "$bk_cache/ccnavi/backup"
		mkdir "$bk_dir"
	) || return 1
	bk_items=".ccnavi/CCNAVI.md .ccnavi/config/common .ccnavi/docs .ccnavi/scripts .claude/settings.json .vscode/settings.json"
	for bk_rel in $bk_items; do
		[ -e "$1/$bk_rel" ] || continue
		(umask 077 && mkdir -p "$bk_dir/$(dirname "$bk_rel")") || return 1
		# -L: リンクは実体をコピーする（リンクのままコピーすると、リンク先が書き換わったあとに戻す元が無くなる）。
		cp -RLp "$1/$bk_rel" "$bk_dir/$bk_rel" || return 1
	done
	for bk_skill in "$2"/.claude/skills/*/; do
		[ -d "$bk_skill" ] || continue
		bk_name=$(basename "$bk_skill")
		[ -e "$1/.claude/skills/$bk_name" ] || continue
		(umask 077 && mkdir -p "$bk_dir/.claude/skills") || return 1
		cp -RLp "$1/.claude/skills/$bk_name" "$bk_dir/.claude/skills/$bk_name" || return 1
	done
	printf '%s' "$bk_dir"
}

# ソースで git pull --ff-only する（認証は ccnavi_git_auth。尋ねさせない）。副シェルで読む。<ソース>
ipull() {
	. "$1/.ccnavi/scripts/ccnavi-common-host.sh"
	ccnavi_git_auth "$1"
	GIT_TERMINAL_PROMPT=0
	GCM_INTERACTIVE=never
	export GIT_TERMINAL_PROMPT GCM_INTERACTIVE
	git -C "$1" pull --ff-only
}

# ---- 経路

ibuild() {
	# --build。<ws> <ソース>
	b_ws=$1
	b_src=$2
	if [ "$check" = yes ]; then
		printf '案内: --build は何も配らない。ソースで実行ファイルを組み立てるだけで、次が起きる\n'
		printf '案内: ソース %s で git pull --ff-only する\n' "$b_src"
		printf '案内: dist/・build/・ソースの .ccnavi/bin/ が書き換わる\n'
		if [ "$with_extensions" = yes ]; then
			printf '案内: --with-extensions なので、pnpm install、extensions/vscode/ccnavi-board/package.json の版の書き換え（未コミットの変更として残る）、この機械の VS Code への拡張機能のインストールも行う\n'
		fi
		inext "ユーザの了承を得てから、--check を外して打つ（$(iq "$b_src/scripts/ccnavi-install.sh") $(iq "$b_ws") --build）"
		return 0
	fi
	if [ "$with_extensions" = yes ]; then
		if ! sh "$b_src/scripts/ccnavi-build-install.sh"; then
			printf '案内: ccnavi-build-install.sh が失敗した。出力の理由を直してから打ち直す\n'
			inext "上の出力の理由を直してから、同じコマンドを打ち直す"
			return 4
		fi
	else
		if [ -d "$b_src/.git" ] && git -C "$b_src" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
			printf '%s\n' "== ソースの最新を取り込み =="
			if ! (ipull "$b_src"); then
				printf '案内: fast-forward で取り込めなかった。ソースのブランチをリモートとそろえてから打ち直す\n'
				inext "ソースのブランチをリモートとそろえるようユーザに頼み、同じコマンドを打ち直す"
				return 4
			fi
		fi
		printf '%s\n' "== ccnavi の組み立て =="
		if ! (cd "$b_src" && uv run --with pyinstaller python build.py); then
			printf '案内: 組み立てに失敗した\n'
			inext "上の出力の理由を直してから、同じコマンドを打ち直す"
			return 4
		fi
	fi
	inext "$(printf 'sh %s %s --from %s --check' "$(iq "$b_src/scripts/ccnavi-install.sh")" "$(iq "$b_ws")" "$(iq "$b_src")")"
	return 0
}

# ---- main

main() {
	check=no
	build=no
	from=""
	source=""
	mode_opt=""
	ticket_opt=""
	force=no
	no_vscode=no
	no_fetch=no
	no_idle=no
	clear_q=no
	with_extensions=no
	ws=""
	for ia in "$@"; do
		install_argline="$install_argline $(iq "$ia")"
	done
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--from)
			[ "$#" -ge 2 ] || die "--from に値がありません。"
			from=$2
			shift 2
			;;
		--source)
			[ "$#" -ge 2 ] || die "--source に値がありません。"
			source=$2
			shift 2
			;;
		--mode)
			[ "$#" -ge 2 ] || die "--mode に値がありません。"
			mode_opt=$2
			shift 2
			;;
		--ticket-control)
			[ "$#" -ge 2 ] || die "--ticket-control に値がありません。"
			ticket_opt=$2
			shift 2
			;;
		--force)
			force=yes
			shift
			;;
		--no-vscode)
			no_vscode=yes
			shift
			;;
		--no-fetch)
			no_fetch=yes
			shift
			;;
		--no-idle)
			no_idle=yes
			shift
			;;
		--clear-quarantine)
			clear_q=yes
			shift
			;;
		--check)
			check=yes
			shift
			;;
		--build)
			build=yes
			shift
			;;
		--with-extensions)
			with_extensions=yes
			shift
			;;
		-h | --help | help)
			usage
			return 0
			;;
		-*)
			die "$1 は指定できないオプションです。指定できるものは --help で確認できます。"
			;;
		*)
			[ -z "$ws" ] || die "ワークスペースルートは 1 つだけ指定してください。"
			[ -n "$1" ] || die "ワークスペースルートが空です。"
			ws=$1
			shift
			;;
		esac
	done
	[ -n "$ws" ] || die "ワークスペースルートを指定してください。"
	if [ "$build" = yes ]; then
		if [ -n "$from" ] || [ -n "$mode_opt" ] || [ -n "$ticket_opt" ] || [ "$force" = yes ] ||
			[ "$no_vscode" = yes ] || [ "$no_fetch" = yes ] || [ "$no_idle" = yes ] || [ "$clear_q" = yes ]; then
			die "--build は --from・--mode・--ticket-control・--force・--no-vscode・--no-fetch・--no-idle・--clear-quarantine と同時に指定できません。"
		fi
	else
		[ "$with_extensions" = no ] || die "--with-extensions は --build のときだけ指定できます。"
		[ -z "$source" ] || die "--source は --build のときだけ指定できます。"
	fi
	case "$mode_opt" in
	'' | enable | dry-run) ;;
	*) die "--mode に指定できるのは enable か dry-run です。" ;;
	esac
	case "$ticket_opt" in
	'' | enable | disable) ;;
	*) die "--ticket-control に指定できるのは enable か disable です。" ;;
	esac
	[ -d "$ws" ] || die "$ws というディレクトリがありません。"
	ws=$(iabs "$ws")

	install_tmp=${TMPDIR:-/tmp}/ccnavi-install.$$
	install_try=0
	while ! (umask 077 && mkdir "$install_tmp") 2>/dev/null; do
		install_try=$((install_try + 1))
		install_tmp=""
		[ "$install_try" -lt 20 ] || die "作業用のディレクトリを作れません。" 4
		install_tmp=${TMPDIR:-/tmp}/ccnavi-install.$$.$install_try
	done
	trap cleanup EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM

	# 配布元（--build ではソース）を決める。
	if [ "$build" = yes ]; then
		base=${source:-$(dirname "$0")/..}
		[ -d "$base" ] || die "$base というディレクトリがありません。"
		src=$(iabs "$base")
		if [ ! -f "$src/build.py" ] || [ ! -f "$src/scripts/ccnavi-doctor.sh" ]; then
			inext "ccnavi のソースのフォルダを指して打ち直す（--source。$src に build.py か scripts/ が無い）"
			return 3
		fi
	else
		base=${from:-$(dirname "$0")/..}
		if [ -f "$base" ]; then
			die "--from にファイルが渡されました。zip は受けません。展開してから、展開したフォルダを --from に渡してください。"
		fi
		[ -d "$base" ] || die "$base というディレクトリがありません。"
		ir_rc=0
		src=$(iresolve_base "$base") || ir_rc=$?
		if [ "$ir_rc" -ne 0 ]; then
			printf '案内: %s\n' "$src"
			inext "配布元のフォルダ（scripts/ccnavi-setup.sh を直下に持つもの）を確かめて、--from に渡して打ち直す"
			return 3
		fi
	fi
	# 入れる先が配布元そのもの、または配布元の中であってはならない（配布元を書き換えてしまう）。
	# リンクを経た別の綴りも同じ場所と見るため、物理パスで比べる。
	src_phys=$(ifold "$(iphys "$src")")
	ws_phys=$(ifold "$(iphys "$ws")")
	case "$ws_phys/" in
	"$src_phys"/*)
		printf '案内: 入れる先（%s）が配布元（%s）と同じか、配布元の中にある\n' "$ws" "$src"
		inext "入れる先に、配布元の外の別のフォルダを指して打ち直す"
		return 3
		;;
	esac
	[ -f "$src/scripts/ccnavi-doctor.sh" ] || {
		printf '案内: %s/scripts/ccnavi-doctor.sh が無い\n' "$src"
		inext "配布元を受け取り直してもらう（scripts/ccnavi-doctor.sh が入っていない）"
		return 3
	}
	[ -f "$src/scripts/ccnavi-setup.sh" ] || {
		printf '案内: %s/scripts/ccnavi-setup.sh が無い\n' "$src"
		inext "配布元を受け取り直してもらう（scripts/ccnavi-setup.sh が入っていない）"
		return 3
	}

	# 入れてよい状態か。jq が無いときは判定できないが、doctor が jq を ng にして止める。
	if command -v jq >/dev/null 2>&1; then
		sm_rc=0
		stored=$(istored_mode "$ws") || sm_rc=$?
		if [ "$sm_rc" -ne 0 ]; then
			printf '案内: %s が JSON として読めない\n' "$stored"
			inext "ユーザに $stored の JSON を直してもらって返事を待ち、同じコマンドを打ち直す"
			return 3
		fi
		# CLAUDECODE は、Claude Code の Bash ツールの中で打たれていることの目印。誤操作を防ぐためのもので、
		# 守りではない（環境変数は消せる）。保護された設定を、Claude Code の中から enable のまま書き換えさせない。
		# ccnavi は dry-run 以外の値（enable・enabled・disable・空白つき・読めない値）を enable として扱う。
		# 値が無いときは、ccnavi がまだ入っていないので止めない。起動した環境の CCNAVI_MODE も見る。
		env_mode=$(printf '%s' "${CCNAVI_MODE:-}" | tr 'A-Z' 'a-z' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
		protected=no
		if [ -n "$stored" ] && [ "$stored" != dry-run ]; then
			protected=yes
		fi
		if [ -n "$env_mode" ] && [ "$env_mode" != dry-run ]; then
			protected=yes
		fi
		if [ "$protected" = yes ] && [ -n "${CLAUDECODE:-}" ]; then
			printf '案内: 入れる先は CCNAVI_MODE が dry-run ではなく（enable として扱われる）、ccnavi の保護が効いている。Claude Code の中からは入れない\n'
			inext "ユーザに、Claude Code の外のターミナルで同じコマンドを打ってもらう（sh $(iq "$0")${install_argline}）"
			return 3
		fi
	fi

	# 状況を調べる。
	set -- --workspace "$ws" --from "$src" --no-next
	[ "$build" = yes ] && set -- "$@" --mode build
	[ "$with_extensions" = yes ] && set -- "$@" --with-extensions
	dr_rc=0
	sh "$src/scripts/ccnavi-doctor.sh" "$@" >"$install_tmp/doctor.out" 2>"$install_tmp/doctor.err" || dr_rc=$?
	ishow "$install_tmp/doctor.out" "$install_tmp/doctor.err"
	if [ "$dr_rc" -ne 0 ]; then
		retry="sh $(iq "$src/scripts/ccnavi-doctor.sh") --workspace $(iq "$ws") --from $(iq "$src")"
		[ "$build" != yes ] || retry="$retry --mode build"
		[ "$with_extensions" != yes ] || retry="$retry --with-extensions"
		inext "ng の項目をユーザに直してもらって返事を待ち、doctor を打ち直す（${retry}）"
		return 3
	fi

	if [ "$build" = yes ]; then
		ibuild "$ws" "$src"
		return $?
	fi

	# ビルド済み
	set -- "$ws" --deploy "$src"
	[ -z "$mode_opt" ] || set -- "$@" --mode "$mode_opt"
	[ -z "$ticket_opt" ] || set -- "$@" --ticket-control "$ticket_opt"
	[ "$force" != yes ] || set -- "$@" --force
	[ "$no_vscode" != yes ] || set -- "$@" --no-vscode
	[ "$no_fetch" != yes ] || set -- "$@" --no-fetch
	[ "$no_idle" != yes ] || set -- "$@" --no-idle

	quarantined=no
	if grep -q '^check: package warn quarantined' "$install_tmp/doctor.out"; then
		quarantined=yes
	fi

	if [ "$check" = yes ]; then
		if [ "$quarantined" = yes ] && [ "$clear_q" != yes ]; then
			printf '案内: 実行ファイルに隔離の印（com.apple.quarantine）が付いている。本番では --clear-quarantine が要る。外すかはユーザが決める\n'
		fi
		ck_rc=0
		sh "$src/scripts/ccnavi-setup.sh" "$@" --check >"$install_tmp/setup.out" 2>"$install_tmp/setup.err" || ck_rc=$?
		ishow "$install_tmp/setup.out" "$install_tmp/setup.err"
		case "$ck_rc" in
		0)
			inext "すでに揃っている。入れ直す必要は無いとユーザに伝える"
			return 0
			;;
		1)
			if [ "$force" = yes ]; then
				printf '案内: --force で入れ替わるファイルを必ずユーザに見せる（references/update.md）\n'
			fi
			inext "一覧をユーザに見せて了承を得てから、--check を外して打つ"
			return 1
			;;
		*)
			inext "setup の出力の理由を直してから打ち直す"
			return 4
			;;
		esac
	fi

	# 本番。--force のときは、setup の前にバックアップを取る。
	if [ "$force" = yes ]; then
		bk_rc=0
		backup=$(ibackup "$ws" "$src") || bk_rc=$?
		if [ "$bk_rc" -ne 0 ]; then
			printf '案内: バックアップを作れなかった（置き場が既にある、入れる先の中になる、または書けない）。何も入れていない\n'
			inext "バックアップの置き場（XDG_CACHE_HOME か ~/.cache）を確かめて打ち直す"
			return 4
		fi
		printf '案内: --force の前に、入れ替わるファイルのバックアップを %s に取った（権限 700）\n' "$backup"
		printf '案内: 戻すときは、ファイルは cp -R %s %s、ディレクトリは cp -R %s %s の形で打つ（<パス> はバックアップの下の相対パス）。入れる先が enable なら Claude Code の外のターミナルで打つ\n' \
			"$(iq "$backup/<パス>")" "$(iq "$ws/<パス>")" "$(iq "$backup/<パス>/.")" "$(iq "$ws/<パス>/")"
	fi

	st_rc=0
	sh "$src/scripts/ccnavi-setup.sh" "$@" >"$install_tmp/setup.out" 2>"$install_tmp/setup.err" || st_rc=$?
	ishow "$install_tmp/setup.out" "$install_tmp/setup.err"
	if [ "$st_rc" -ne 0 ]; then
		printf '案内: ccnavi-setup.sh が %s で終わった。入れる先は書き換わっていないか、一部だけ\n' "$st_rc"
		inext "setup の出力の理由を直してから打ち直す"
		return 4
	fi

	# 入れる先の後始末（配布元には触らない）。
	target=$(sed -n 1p "$src/dist/ccnavi.target" | tr -d '\r')
	bin_dir="$ws/.ccnavi/bin/$target"
	for exe_name in ccnavi ccnavi.exe; do
		if [ -f "$bin_dir/$exe_name" ] && [ ! -x "$bin_dir/$exe_name" ]; then
			if ! chmod +x "$bin_dir/$exe_name"; then
				printf '案内: %s に実行ビットを付けられなかった\n' "$bin_dir/$exe_name"
				inext "実行ビットを付けられない理由（権限・ファイルシステム）を直して打ち直す"
				return 4
			fi
		fi
	done
	if command -v xattr >/dev/null 2>&1; then
		for exe_name in ccnavi ccnavi.exe; do
			[ -f "$bin_dir/$exe_name" ] || continue
			if xattr -p com.apple.quarantine "$bin_dir/$exe_name" >/dev/null 2>&1; then
				if [ "$clear_q" = yes ]; then
					xattr -dr com.apple.quarantine "$bin_dir" >/dev/null 2>&1 || :
					if xattr -p com.apple.quarantine "$bin_dir/$exe_name" >/dev/null 2>&1; then
						printf '案内: 隔離の印を外せなかった: %s\n' "$bin_dir"
						inext "隔離の印を外せない理由を直して打ち直す"
						return 4
					fi
				else
					printf '案内: 入れた実行ファイルに隔離の印（com.apple.quarantine）が付いている。macOS が初回の起動を止めることがある。外すなら --clear-quarantine を付けて打ち直す（ダウンロードしたものを信頼する意味になるので、ユーザが決める）\n'
				fi
			fi
		done
	fi

	# lint。起動できなければ 4、error があれば 5。
	li_rc=0
	sh "$ws/.ccnavi/scripts/ccnavi-launcher.sh" --lint --root "$ws" --log "" --state "" >"$install_tmp/lint.out" 2>"$install_tmp/lint.err" || li_rc=$?
	ishow "$install_tmp/lint.out" "$install_tmp/lint.err"
	case "$li_rc" in
	0 | 1) ;;
	*)
		printf '案内: lint を起動できなかった（終了コード %s）。実行ファイルがこの機械で動かない可能性がある\n' "$li_rc"
		inext "lint の出力の理由を直してから、同じコマンドを打ち直す"
		return 4
		;;
	esac
	if [ "$li_rc" -eq 0 ]; then
		final_mode=$(istored_mode "$ws" 2>/dev/null || :)
		case "$final_mode" in
		'' | dry-run)
			printf '案内: dry-run では lint の warn が出る。モードが dry-run、allow が空の 2 件は正常\n'
			;;
		esac
	fi

	# 最後の案内。
	itodos "$install_tmp/doctor.out"
	vsix_found=""
	for vs in "$src"/dist/ccnavi-board-*.vsix; do
		[ -f "$vs" ] && vsix_found=$vs
	done
	if [ -n "$vsix_found" ]; then
		printf '案内: VS Code 拡張は、ユーザが code --install-extension %s で入れる\n' "$(iq "$vsix_found")"
	fi
	chrome_dir="$src/extensions/chrome/ccnavi-approval/dist"
	if [ -f "$chrome_dir/manifest.json" ]; then
		printf '案内: Chrome 拡張は、ユーザが chrome://extensions の「パッケージ化されていない拡張機能を読み込む」で %s を選ぶ（このフォルダは消さずに置く）\n' "$chrome_dir"
	fi
	if [ -n "$vsix_found" ] || [ -f "$chrome_dir/manifest.json" ]; then
		printf '案内: 実行ファイル・sh・拡張機能はそろえて更新する決まりがある。片方だけ更新しない\n'
	fi
	if [ "$li_rc" -eq 1 ]; then
		printf '案内: lint に error がある。ccnavi-config スキル（配布元の .claude/skills/ccnavi-config）で直す。配布先に無ければ ~/.claude/skills/ にコピーするか、配布元で Claude Code を起動して使う\n'
		inext "lint の error を直す（ccnavi-config スキル）"
		return 5
	fi
	inext "ユーザに Claude Code を終了して、$ws で起動し直してもらう"
	return 0
}

main "$@"; exit $?
