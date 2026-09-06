#!/bin/sh
# PostToolUse (Write|Edit|MultiEdit): keep the Go tree formatted, vetted and linted.
#
# Runs only when the edited file is Go source. Reports through exit 2, which is
# the one channel on this event that reaches the model, so a violation gets
# fixed in the same turn instead of surfacing at commit time.
#
# The report stands on its own: hooks on one event run in parallel and in no
# fixed order, so it never refers to what another hook decided.

payload=$(cat)

# Extract tool_input.file_path without a JSON parser; only the extension is read,
# so backslashes in a Windows path do not matter.
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file" in
*.go) ;;
*) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

report=""
add() { report="${report}$1
"; }

if unformatted=$(gofmt -l . 2>&1) && [ -n "$unformatted" ]; then
	add "gofmt: これらのファイルが整形されていません。'gofmt -w <file>' を実行してください:
$unformatted"
fi

if vet=$(go vet ./... 2>&1); then
	:
else
	add "go vet:
$vet"
fi

if command -v golangci-lint >/dev/null 2>&1; then
	if lint=$(golangci-lint run 2>&1); then
		:
	else
		add "golangci-lint:
$lint"
	fi
fi

# Keep the registered binary in step with the source, so the guard that runs on
# the next tool call is the one just edited. Windows refuses to overwrite a
# running executable but allows renaming it, so the old one is moved aside first.
if build=$(go build -o ccnavi.new.exe . 2>&1); then
	mv -f ccnavi.exe ccnavi.old.exe 2>/dev/null
	mv -f ccnavi.new.exe ccnavi.exe
	rm -f ccnavi.old.exe 2>/dev/null
else
	add "go build:
$build"
fi

[ -z "$report" ] && exit 0

printf '%s\n' "$report" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2
