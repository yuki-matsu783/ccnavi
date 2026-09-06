// Command ccnavi guards Claude Code tool calls and guides the agent toward a
// workable alternative when it blocks one.
//
// It is registered as a hook command. One invocation reads a JSON payload from
// stdin, writes a response to stdout, and exits.
package main

import (
	"context"
	"os"

	"github.com/yuki-matsu783/ccnavi/internal/cli"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), cli.Deadline)
	defer cancel()

	os.Exit(cli.Run(ctx, os.Stdin, os.Stdout, os.Stderr, os.Args[1:]))
}
