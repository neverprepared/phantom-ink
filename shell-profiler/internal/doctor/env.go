package doctor

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
)

// Well-known files that make up a profile.
const (
	// EnvFileName is the profile env file.
	EnvFileName = ".env"
	// ExampleEnvFileName is the example env file — the per-profile source of
	// truth for which keys are expected.
	ExampleEnvFileName = ".env.example"
	// SecretsEnvFileName is the secrets env file. It may be a 1Password FIFO.
	SecretsEnvFileName = ".env.secrets"
	// EnvrcFileName is the direnv file.
	EnvrcFileName = ".envrc"
	// GitconfigFileName is the per-profile git config.
	GitconfigFileName = ".gitconfig"
)

// errUnreadable signals an env file that exists but cannot be read safely
// (e.g. a 1Password FIFO with no writer attached).
var errUnreadable = errors.New("env file not readable")

// secretsReadTimeout bounds reads of the secrets env file. A 1Password FIFO
// with no writer would otherwise block forever.
const secretsReadTimeout = 500 * time.Millisecond

// ParseEnv parses env-file content into a key/value map.
//
// It understands `KEY=value`, a leading `export `, `#` comments, and single or
// double quoted values. Values are returned so they can be handed to
// subprocesses — callers must never print them.
func ParseEnv(content string) map[string]string {
	out := map[string]string{}
	sc := bufio.NewScanner(strings.NewReader(content))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		eq := strings.IndexByte(line, '=')
		if eq <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		if key == "" {
			continue
		}
		val := strings.TrimSpace(line[eq+1:])
		val = stripInlineComment(val)
		val = unquote(val)
		out[key] = val
	}
	return out
}

// stripInlineComment removes a trailing ` # ...` comment from an unquoted value.
func stripInlineComment(v string) string {
	if strings.HasPrefix(v, "\"") || strings.HasPrefix(v, "'") {
		return v
	}
	if i := strings.Index(v, " #"); i >= 0 {
		return strings.TrimSpace(v[:i])
	}
	return v
}

func unquote(v string) string {
	if len(v) >= 2 {
		if (v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'') {
			return v[1 : len(v)-1]
		}
	}
	return v
}

// ReadEnvFile reads and parses an env file.
//
// It is deliberately defensive: a FIFO (the 1Password secrets pattern) is never
// read inline, and every read is bounded by secretsReadTimeout, so doctor can
// never hang on a pipe with no writer. An unreadable file yields errUnreadable,
// which callers translate into a skip — never a failure.
func ReadEnvFile(path string) (map[string]string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeNamedPipe != 0 {
		return nil, fmt.Errorf("%w: %s is a FIFO", errUnreadable, filepath.Base(path))
	}
	if info.IsDir() {
		return nil, fmt.Errorf("%w: %s is a directory", errUnreadable, filepath.Base(path))
	}

	type readResult struct {
		data []byte
		err  error
	}
	ch := make(chan readResult, 1)
	go func() {
		// O_NONBLOCK keeps the open() itself from blocking if the file turned
		// into a pipe between the stat and the open.
		f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
		if err != nil {
			ch <- readResult{nil, err}
			return
		}
		defer func() { _ = f.Close() }()
		data, err := readAllLimited(f)
		ch <- readResult{data, err}
	}()

	select {
	case res := <-ch:
		if res.err != nil {
			return nil, fmt.Errorf("%w: %v", errUnreadable, res.err)
		}
		return ParseEnv(string(res.data)), nil
	case <-time.After(secretsReadTimeout):
		return nil, fmt.Errorf("%w: %s timed out (FIFO with no writer?)", errUnreadable, filepath.Base(path))
	}
}

// readAllLimited reads at most 1MiB — env files are small, and a runaway
// stream should not be swallowed whole.
func readAllLimited(f *os.File) ([]byte, error) {
	const limit = 1 << 20
	return io.ReadAll(io.LimitReader(f, limit))
}

// MissingKeys returns the keys that the example env file declares but the
// profile env file is missing entirely, and those that are present but empty.
// Both slices are sorted for stable output.
func MissingKeys(profileEnv, exampleEnv map[string]string) (missing, empty []string) {
	for key := range exampleEnv {
		val, present := profileEnv[key]
		switch {
		case !present:
			missing = append(missing, key)
		case strings.TrimSpace(val) == "":
			empty = append(empty, key)
		}
	}
	sort.Strings(missing)
	sort.Strings(empty)
	return missing, empty
}
