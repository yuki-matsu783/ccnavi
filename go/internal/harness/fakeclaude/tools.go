package fakeclaude

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

// toolFailed はツールが失敗したこと。text は tool_result の文。
type toolFailed struct{ text string }

type toolFunc func(s *session, in object) (string, object, *toolFailed)

var tools = map[string]toolFunc{"Read": readTool, "Write": writeTool, "Edit": editTool, "Bash": bashTool}

// strerror は OS の失敗の理由（Python の OSError.strerror の形）。
func strerror(err error) string {
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return "No such file or directory"
	case errors.Is(err, fs.ErrPermission):
		return "Permission denied"
	}
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return errno.Error()
	}
	return err.Error()
}

func readTool(_ *session, in object) (string, object, *toolFailed) {
	path := asString(in["file_path"])
	data, err := os.ReadFile(path)
	if err != nil {
		return "", nil, &toolFailed{"File does not exist. " + strerror(err)}
	}
	content := string(data)
	lines := strings.Split(content, "\n")
	shown := make([]string, len(lines))
	for i, line := range lines {
		shown[i] = fmt.Sprintf("%d\t%s", i+1, line)
	}
	return strings.Join(shown, "\n"), object{
		"type": "text",
		"file": object{
			"filePath":   path,
			"content":    content,
			"numLines":   len(lines),
			"startLine":  1,
			"totalLines": len(lines),
		},
	}, nil
}

func writeTool(_ *session, in object) (string, object, *toolFailed) {
	path := asString(in["file_path"])
	content := asString(in["content"])
	var original any
	if data, err := os.ReadFile(path); err == nil {
		original = string(data)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", nil, &toolFailed{strerror(err)}
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", nil, &toolFailed{strerror(err)}
	}
	kind, text := "create", "File created successfully at: "+path
	if original != nil {
		kind, text = "update", "The file "+path+" has been updated successfully."
	}
	return text, object{
		"type":            kind,
		"filePath":        path,
		"content":         content,
		"structuredPatch": []any{},
		"originalFile":    original,
		"userModified":    false,
	}, nil
}

func editTool(_ *session, in object) (string, object, *toolFailed) {
	path, old, repl := asString(in["file_path"]), asString(in["old_string"]), asString(in["new_string"])
	data, err := os.ReadFile(path)
	if err != nil {
		return "", nil, &toolFailed{"File does not exist."}
	}
	original := string(data)
	count := 0
	if old != "" {
		count = strings.Count(original, old)
	}
	if count == 0 {
		return "", nil, &toolFailed{"String to replace not found in file.\nString: " + old}
	}
	all, _ := in["replace_all"].(bool)
	if count > 1 && !all {
		return "", nil, &toolFailed{fmt.Sprintf("Found %d matches of the string to replace, but replace_all is false.", count)}
	}
	if _, ok := in["replace_all"]; !ok {
		in["replace_all"] = false
	}
	n := 1
	if all {
		n = -1
	}
	if err := os.WriteFile(path, []byte(strings.Replace(original, old, repl, n)), 0o644); err != nil {
		return "", nil, &toolFailed{strerror(err)}
	}
	return "The file " + path + " has been updated successfully.", object{
		"filePath":        path,
		"oldString":       old,
		"newString":       repl,
		"originalFile":    original,
		"structuredPatch": []any{},
		"userModified":    false,
		"replaceAll":      all,
	}, nil
}

func bashTool(s *session, in object) (string, object, *toolFailed) {
	cmd := exec.CommandContext(context.Background(), "sh", "-c", asString(in["command"]))
	cmd.Dir = s.root
	cmd.Env = s.env
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		return "", nil, &toolFailed{"Exit code 127\n" + err.Error()}
	}
	out := strings.TrimRight(stdout.String()+stderr.String(), "\n")
	if code := cmd.ProcessState.ExitCode(); code != 0 {
		text := fmt.Sprintf("Exit code %d", code)
		if out != "" {
			text += "\n" + out
		}
		return "", nil, &toolFailed{text}
	}
	return out, object{
		"stdout":           strings.TrimRight(stdout.String(), "\n"),
		"stderr":           strings.TrimRight(stderr.String(), "\n"),
		"interrupted":      false,
		"isImage":          false,
		"noOutputExpected": false,
	}, nil
}
