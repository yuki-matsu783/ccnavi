#!/bin/sh
# ccnavi-package は、配る側が ccnavi の配布用の zip を作る。ccnavi のリポジトリ（ソース）で打つ。
#
#   sh scripts/ccnavi-package.sh [--build] [--out <dir>]
#
#   --build        先に build.py（uv run --with pyinstaller python build.py）で組み立てる
#   --out <dir>    zip と .sha256 の置き場。既定は build/package/
#
# build/package/ は毎回空にしてから、build/package/ccnavi-<短いコミット>-<target>/ に集める。
# 組み立て済みの dist/ccnavi/ の版（--version のコミット）が HEAD と違えば 2 で止める（--build を付ける）。
# 未コミットの変更があれば名前に -dirty を付ける。
#
# 入れるもの: dist/ccnavi/・dist/ccnavi.target、今の版の VS Code 拡張（dist/ccnavi-board-<版>.vsix）、
# 版と互換の版が合う Chrome 拡張（extensions/chrome/ccnavi-approval/dist/）、
# .ccnavi/CCNAVI.md・.ccnavi/config/common/・.ccnavi/scripts/・.ccnavi/docs/・.claude/skills/、
# scripts/ccnavi-{setup,install,doctor}.sh、新しく書く VERSION と INSTALL.md。
# 入れないもの: .ccnavi/config/project/・logs/・wip/・tests/・src/・**/ccnavi-index.jsonl・**/.DS_Store。
#
# 拡張機能の照合
#   VS Code: package.json の今の版の vsix だけを入れ、拡張の EXTENSION_COMPAT
#     （extensions/vscode/ccnavi-board/src/core/version.ts）が実行ファイルの COMPAT
#     （src/ccnavi/entry/version.py）と同じときだけ入れる。vsix の中身が今のソースから作られたか
#     （同じ版で古いまま）は見分けられない。拡張を直したら版を上げる決まりに頼る
#   Chrome: dist/manifest.json の version が package.json の version と同じで、組み立てのときに
#     埋め込まれる互換の版（dist/background.js の compat:<数>）が COMPAT と同じときだけ入れる
#
# zip: zip コマンドがあれば zip -r -y（リンクを保つ）。無ければ python -m zipfile。python の zipfile は
# リンクを保てないので、集めた中にシンボリックリンクがあれば作らずに 2 で止める。
# zip の隣に <zip 名>.sha256 を書く。
#
# 終了コード: 0 成功 / 2 引数の誤り・前提が足りない / 4 途中で失敗

set -eu

usage() {
	printf '%s\n' \
		'sh scripts/ccnavi-package.sh [--build] [--out <dir>]' \
		'' \
		'  --build      先に build.py で組み立てる' \
		'  --out <dir>  zip と .sha256 の置き場（既定 build/package/）' \
		'終了コード: 0 成功 / 2 引数の誤り・前提が足りない / 4 途中で失敗'
}

die() {
	printf 'ccnavi-package: %s\n' "$1" >&2
	exit "${2:-2}"
}

pq() {
	pq_rest=$1
	pq_out=""
	while :; do
		case "$pq_rest" in
		*\'*)
			pq_out="${pq_out}${pq_rest%%\'*}'\"'\"'"
			pq_rest=${pq_rest#*\'}
			;;
		*) break ;;
		esac
	done
	printf "'%s%s'" "$pq_out" "$pq_rest"
}

pabs() {
	(cd -- "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })
}

# 値を JSON から読む（jq）。<ファイル> <jq の式>。読めなければ 1（壊れた JSON を「無い」に化けさせない）
pjq() {
	pj_out=$(jq -r "$2" "$1") || return 1
	printf '%s\n' "$pj_out" | tr -d '\r'
}

# INSTALL.md を書く。<出力先> <zip 名> <target> <コミット>
write_install_md() {
	printf '%s\n' \
		"# ccnavi の導入（$2）" \
		'' \
		"この zip は ccnavi のビルド済みの配布物です（$3、コミット $4）。" \
		'' \
		'## 1. チェックサムを確かめる' \
		'' \
		'zip と同じ場所にある `.sha256` の値と、次の結果を見比べます。一致しなければ展開せず、受け取り直します。' \
		'' \
		'- macOS: `shasum -a 256 <zip>`' \
		'- Linux: `sha256sum <zip>`' \
		'- Windows（PowerShell かコマンドプロンプト）: `certutil -hashfile <zip> SHA256`（空白の有無と大文字小文字は無視して見比べる）' \
		'' \
		'## 2. 展開する' \
		'' \
		'- Windows: エクスプローラーで zip を右クリックして「すべて展開」' \
		'- macOS: Finder でダブルクリック、または `unzip <zip>`' \
		'- Linux: `unzip <zip>`' \
		'' \
		"自分の機械に合う zip かを、名前の \`<os>-<arch>\` で確かめます（この zip は $3）。" \
		'' \
		'## 3. 入れる' \
		'' \
		'### Claude Code に頼む' \
		'' \
		'展開したフォルダで `claude` を起動し、「ccnavi を入れて」と頼みます。`ccnavi-install` スキルが、状況を調べ、足りないものの入れ方を案内してから入れます。' \
		'' \
		'### 自分で打つ' \
		'' \
		'展開したフォルダで、まず何が起きるかを確かめ（`--check`）、問題が無ければ `--check` を外して打ちます。' \
		'' \
		'    sh scripts/ccnavi-doctor.sh --workspace <入れる先>' \
		'    sh scripts/ccnavi-install.sh <入れる先> --check' \
		'    sh scripts/ccnavi-install.sh <入れる先>' \
		'' \
		'最初は dry-run で入ります。記録を読んでルールを直してから、`--mode enable` に切り替えます。' \
		'' \
		'### スキルを ~/.claude/skills/ にコピーする' \
		'' \
		'どのフォルダからでも「ccnavi を入れて」と頼めるようにするには、`.claude/skills/ccnavi-install` を `~/.claude/skills/` にコピーします。' \
		'コピーしたスキルは、展開したフォルダの場所をユーザに聞いてから進みます。' \
		'' \
		'## 拡張機能' \
		'' \
		'同梱されていれば、`dist/ccnavi-board-<版>.vsix`（VS Code: `code --install-extension <ファイル>`）と' \
		'`extensions/chrome/ccnavi-approval/dist/`（Chrome: chrome://extensions の「パッケージ化されていない拡張機能を読み込む」）があります。' \
		'実行ファイル・sh・拡張機能はそろえて更新します。' \
		>"$1"
}

main() {
	build=no
	out=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--build)
			build=yes
			shift
			;;
		--out)
			[ "$#" -ge 2 ] || die "--out に値がありません。"
			[ -n "$2" ] || die "--out が空です。"
			out=$2
			shift 2
			;;
		-h | --help | help)
			usage
			return 0
			;;
		*)
			die "$1 は使えない引数です。使えるものは --help で確認できます。"
			;;
		esac
	done

	root=$(pabs "$(dirname "$0")/..")
	[ -f "$root/build.py" ] || die "$root は ccnavi のソースに見えません（build.py が無い）。ソースの scripts/ccnavi-package.sh を打ってください。"
	command -v jq >/dev/null 2>&1 || die "jq が必要です。"
	command -v git >/dev/null 2>&1 || die "git が必要です。"
	if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
		die "sha256sum か shasum が必要です（チェックサムを書くため）。"
	fi

	if [ "$build" = yes ]; then
		if ! (cd "$root" && uv run --with pyinstaller python build.py); then
			die "build.py が失敗しました。" 4
		fi
	fi

	target_file="$root/dist/ccnavi.target"
	[ -f "$target_file" ] || die "$target_file がありません。--build を付けて打ち直してください（build.py が書きます）。"
	target=$(sed -n 1p "$target_file" | tr -d '\r')
	case "$target" in
	'' | *[!a-z0-9_-]*) die "$target_file の中身が読めません。--build を付けて打ち直してください。" ;;
	esac
	exe=""
	for pk_name in ccnavi ccnavi.exe; do
		if [ -f "$root/dist/ccnavi/$pk_name" ]; then
			exe="$root/dist/ccnavi/$pk_name"
			break
		fi
	done
	[ -n "$exe" ] || die "$root/dist/ccnavi/ に実行ファイル（ccnavi か ccnavi.exe）がありません。--build を付けて打ち直してください。"

	# 版を照合する。組み立てた実行ファイルが今の HEAD から作られたものかを見る。
	exe_info=$("$exe" --version </dev/null 2>/dev/null) || die "$exe --version が失敗しました。--build を付けて組み立て直してください。"
	exe_commit=$(printf '%s\n' "$exe_info" | sed -n 's/^commit:[[:space:]]*\([^[:space:]]*\)[[:space:]]*$/\1/p' | sed -n 1p)
	exe_compat=$(printf '%s\n' "$exe_info" | sed -n 's/^compat:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' | sed -n 1p)
	exe_version=$(printf '%s\n' "$exe_info" | sed -n '1p' | tr -d '\r')
	head_commit=$(git -C "$root" rev-parse HEAD 2>/dev/null) || die "$root の HEAD を読めません（git のリポジトリではない）。"
	if [ "${exe_commit%-dirty}" != "$head_commit" ]; then
		die "実行ファイルのコミット（${exe_commit:-不明}）が HEAD（${head_commit}）と違います。--build を付けて組み立て直してください。"
	fi
	dirty=no
	if [ -n "$(git -C "$root" status --porcelain 2>/dev/null)" ] || [ "$exe_commit" != "${exe_commit%-dirty}" ]; then
		dirty=yes
	fi
	short=$(printf '%s' "$head_commit" | cut -c 1-7)
	name="ccnavi-$short-$target"
	if [ "$dirty" = yes ]; then
		name="$name-dirty"
		printf '案内: 未コミットの変更があるか、実行ファイルが未コミットの状態から組み立てられている。zip の名前に -dirty を付けた。配る前にコミットして組み立て直すこと\n'
	fi

	pkg_root="$root/build/package"
	if [ -z "$out" ]; then
		out_dir=$pkg_root
	else
		mkdir -p -- "$out" || die "$out を作れません。" 4
		out_dir=$(pabs "$out")
	fi
	stage="$pkg_root/$name"
	case "$out_dir/" in
	"$stage"/*) die "--out が集める場所（${stage}）の中です。別の場所を指してください。" ;;
	esac

	# 毎回空にする。前回の残りを入れない。
	rm -rf "$pkg_root"
	mkdir -p "$stage" "$out_dir" || die "$stage を作れません。" 4

	copy_into() {
		# <元（root から）> <先（stage から）>
		mkdir -p "$stage/$(dirname "$2")" || return 1
		cp -RPp "$root/$1" "$stage/$2" || return 1
	}

	copy_into dist/ccnavi dist/ccnavi || die "dist/ccnavi をコピーできません。" 4
	copy_into dist/ccnavi.target dist/ccnavi.target || die "dist/ccnavi.target をコピーできません。" 4
	for item in .ccnavi/CCNAVI.md .ccnavi/config/common .ccnavi/scripts .ccnavi/docs .claude/skills \
		scripts/ccnavi-setup.sh scripts/ccnavi-install.sh scripts/ccnavi-doctor.sh; do
		[ -e "$root/$item" ] || die "$root/$item がありません。" 4
		copy_into "$item" "$item" || die "$item をコピーできません。" 4
	done

	# 実行ファイルの互換の版（拡張機能の照合の基準）
	py_compat=$(sed -n 's/^COMPAT = \([0-9][0-9]*\)$/\1/p' "$root/src/ccnavi/entry/version.py" | sed -n 1p)

	# VS Code 拡張
	vsc_dir="$root/extensions/vscode/ccnavi-board"
	vsc_version=""
	[ ! -f "$vsc_dir/package.json" ] || vsc_version=$(pjq "$vsc_dir/package.json" '.version // empty') ||
		die "$vsc_dir/package.json を JSON として読めません。"
	vsc_compat=$(sed -n 's/^export const EXTENSION_COMPAT = \([0-9][0-9]*\);.*$/\1/p' "$vsc_dir/src/core/version.ts" 2>/dev/null | sed -n 1p)
	vsix="$root/dist/ccnavi-board-$vsc_version.vsix"
	if [ -z "$vsc_version" ] || [ ! -f "$vsix" ]; then
		printf '案内: VS Code 拡張（dist/ccnavi-board-%s.vsix）が無いので入れていない。scripts/ccnavi-build-install.sh で作れる\n' "${vsc_version:-<版>}"
	elif [ -z "$py_compat" ] || [ "$vsc_compat" != "$py_compat" ]; then
		printf '案内: VS Code 拡張の互換の版（%s）が実行ファイルの互換の版（%s）と違うので入れていない\n' "${vsc_compat:-不明}" "${py_compat:-不明}"
	else
		copy_into "dist/ccnavi-board-$vsc_version.vsix" "dist/ccnavi-board-$vsc_version.vsix" || die "vsix をコピーできません。" 4
	fi

	# Chrome 拡張
	chr_dir="$root/extensions/chrome/ccnavi-approval"
	chr_version=""
	[ ! -f "$chr_dir/package.json" ] || chr_version=$(pjq "$chr_dir/package.json" '.version // empty') ||
		die "$chr_dir/package.json を JSON として読めません。"
	chr_built=""
	[ ! -f "$chr_dir/dist/manifest.json" ] || chr_built=$(pjq "$chr_dir/dist/manifest.json" '.version // empty') ||
		die "$chr_dir/dist/manifest.json を JSON として読めません。"
	if [ -z "$chr_built" ]; then
		printf '案内: Chrome 拡張（extensions/chrome/ccnavi-approval/dist/）が無いので入れていない。scripts/ccnavi-build-install.sh で作れる\n'
	elif [ "$chr_built" != "$chr_version" ]; then
		printf '案内: Chrome 拡張の組み立て済みの版（%s）がソースの版（%s）と違うので入れていない。組み立て直すこと\n' "$chr_built" "$chr_version"
	elif [ -z "$py_compat" ] || ! grep -Eq "compat:${py_compat}([^0-9]|\$)" "$chr_dir/dist/background.js" 2>/dev/null; then
		printf '案内: Chrome 拡張の組み込みの互換の版が実行ファイルの互換の版（%s）と合わない（または読めない）ので入れていない。組み立て直すこと\n' "${py_compat:-不明}"
	else
		copy_into extensions/chrome/ccnavi-approval/dist extensions/chrome/ccnavi-approval/dist || die "Chrome 拡張をコピーできません。" 4
	fi

	# 入れないもの（コピーしたものの中に紛れたものを除く）
	find "$stage" \( -name ccnavi-index.jsonl -o -name .DS_Store \) -exec rm -f {} + || die "不要なファイルを除けません。" 4
	# ワークツリーをそのままコピーするので、追跡されていない物や無視された物が紛れうる。キー・認証情報の置き場と、
	# 実行時の記録（logs・wip）、古い vsix は入れない。
	# dist/ccnavi の中の *.pem（証明書の束）は実行ファイルが使うので、キーの掃除は .ccnavi と .claude だけに掛ける。
	find "$stage/.ccnavi" "$stage/.claude" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' \
		-o -name '*.key' -o -name 'credentials*.json' -o -name 'secrets.y*ml' -o -name 'id_rsa' \
		-o -name 'id_ed25519' \) -exec rm -f {} + || die "不要なファイルを除けません。" 4
	find "$stage/.ccnavi" "$stage/.claude" \( -name logs -o -name wip \) -type d -prune -exec rm -rf {} + ||
		die "不要なディレクトリを除けません。" 4
	find "$stage/dist" -maxdepth 2 -type f -name 'ccnavi-board-*.vsix' ! -name "ccnavi-board-$vsc_version.vsix" \
		-exec rm -f {} + || die "不要なファイルを除けません。" 4

	printf '%s\n' "$exe_version" "commit: $head_commit" "target: $target" "compat: ${exe_compat:-不明}" "dirty: $dirty" >"$stage/VERSION" || die "VERSION を書けません。" 4
	write_install_md "$stage/INSTALL.md" "$name" "$target" "$short" || die "INSTALL.md を書けません。" 4

	zip_path="$out_dir/$name.zip"
	# 同じ名前の zip があると、zip は追記する。前回の中身を残さない。
	rm -f "$zip_path" "$zip_path.sha256"
	if command -v zip >/dev/null 2>&1; then
		if ! (cd "$pkg_root" && zip -r -y -q "$zip_path" "$name"); then
			die "zip を作れませんでした。" 4
		fi
	else
		if [ -n "$(find "$stage" -type l)" ]; then
			die "集めた中にシンボリックリンクがあり、zip コマンドが無いので python では作れません（リンクを保てない）。zip を入れてください。"
		fi
		if ! (cd "$pkg_root" && uv run --project "$root" python -m zipfile -c "$zip_path" "$name"); then
			die "zip を作れませんでした。" 4
		fi
	fi

	if command -v sha256sum >/dev/null 2>&1; then
		(cd "$out_dir" && sha256sum "$name.zip") >"$zip_path.sha256" || die ".sha256 を書けません。" 4
	else
		(cd "$out_dir" && shasum -a 256 "$name.zip") >"$zip_path.sha256" || die ".sha256 を書けません。" 4
	fi

	printf 'zip: %s\n' "$zip_path"
	printf 'sha256: %s\n' "$zip_path.sha256"
	printf '案内: zip と .sha256 は、受け取る人が同じ場所から取れるようにセットで配る\n'
	printf '次: 共有フォルダに %s と %s を置く（ユーザの操作）\n' "$(pq "$zip_path")" "$(pq "$zip_path.sha256")"
	return 0
}

main "$@"; exit $?
