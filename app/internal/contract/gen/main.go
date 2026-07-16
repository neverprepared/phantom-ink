//go:build ignore

// Command gen is the codegen driver for the timeline-entry contract bindings.
// It is run by `go generate ./internal/contract` (see ../doc.go), never built
// into the app.
//
// Pipeline (one source, no submodule, no vendored bindings from other repos):
//
//  1. git-fetch schema/timeline-entry.schema.json from neverprepared/phantom-contracts
//     at the pinned tag in ../CONTRACT_TAG (a `git archive` of the single file —
//     no clone, no submodule). This pristine file is written to
//     ../timeline-entry.schema.json and is THE source both the conformance test
//     (embedded) and the Justfile ajv recipe validate against.
//  2. Collapse the pydantic-style `anyOf[{X},{null}]` optionals into plain
//     nullable X, so the Go generator emits `*X` / `*EnvelopeStatus` rather than
//     `interface{}`. This is a lossless-for-Go rewrite of the SAME contract — it
//     changes no validation semantics; it only lets the generator name the types.
//     The collapsed form is a transient codegen input; it is never committed.
//  3. Run github.com/atombender/go-jsonschema (pinned via `go run tool@version`)
//     over the collapsed schema to produce ../envelope.gen.go.
//
// Determinism: the fetched schema is byte-stable at a fixed tag and the
// generator output is stable, so a re-run yields no diff (acceptance criterion).
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const (
	contractsRepo = "git@github.com:neverprepared/phantom-contracts.git"
	schemaPath    = "schema/timeline-entry.schema.json"
	// generatorTool pins the Go binding generator. Bump deliberately — a new
	// generator version can change the emitted Go and break the no-diff check.
	generatorTool = "github.com/atombender/go-jsonschema@v0.23.1"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "contract codegen: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	// This program runs from the package dir (go generate sets $GOFILE's dir as
	// cwd). Resolve paths relative to it.
	pkgDir, err := os.Getwd()
	if err != nil {
		return err
	}

	tag, err := readTag(filepath.Join(pkgDir, "CONTRACT_TAG"))
	if err != nil {
		return err
	}

	// 1. Fetch the single schema file from the pinned tag via `git archive`.
	pristine, err := fetchSchema(tag)
	if err != nil {
		return fmt.Errorf("fetch %s@%s: %w", schemaPath, tag, err)
	}
	schemaOut := filepath.Join(pkgDir, "timeline-entry.schema.json")
	if err := os.WriteFile(schemaOut, pristine, 0o644); err != nil {
		return fmt.Errorf("write pristine schema: %w", err)
	}

	// 2. Collapse nullable anyOf -> nullable type, into a temp file.
	collapsed, err := collapseNullableAnyOf(pristine)
	if err != nil {
		return fmt.Errorf("collapse schema: %w", err)
	}
	tmp, err := os.CreateTemp("", "timeline-entry-collapsed-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(collapsed); err != nil {
		return err
	}
	tmp.Close()

	// 3. Generate Go types.
	genOut := filepath.Join(pkgDir, "envelope.gen.go")
	if err := generate(tmp.Name(), genOut); err != nil {
		return fmt.Errorf("run generator: %w", err)
	}

	fmt.Printf("contract codegen: wrote %s and %s from %s@%s\n",
		filepath.Base(schemaOut), filepath.Base(genOut), schemaPath, tag)
	return nil
}

func readTag(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read pinned tag: %w", err)
	}
	tag := strings.TrimSpace(string(b))
	if tag == "" {
		return "", fmt.Errorf("CONTRACT_TAG is empty")
	}
	return tag, nil
}

// fetchSchema pulls schema/timeline-entry.schema.json at the pinned tag via a
// blobless, treeless shallow clone into a temp dir (no submodule, nothing
// vendored into git). GitHub disables `git archive --remote` over SSH, so a
// depth-1 clone of the single tag is the lightest fetch that works. The temp
// checkout is discarded; only the file bytes are returned.
func fetchSchema(tag string) ([]byte, error) {
	dir, err := os.MkdirTemp("", "phantom-contracts-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)

	clone := exec.Command("git", "clone",
		"--depth", "1",
		"--branch", tag,
		"--filter=blob:none",
		contractsRepo, dir,
	)
	var cloneErr bytes.Buffer
	clone.Stderr = &cloneErr
	if err := clone.Run(); err != nil {
		return nil, fmt.Errorf("git clone %s@%s: %v: %s", contractsRepo, tag, err, cloneErr.String())
	}

	file := filepath.Join(dir, filepath.FromSlash(schemaPath))
	data, err := os.ReadFile(file)
	if err != nil {
		return nil, fmt.Errorf("read %s from checkout: %w", schemaPath, err)
	}
	if len(data) == 0 {
		return nil, fmt.Errorf("%s was empty at %s", schemaPath, tag)
	}
	return data, nil
}

// collapseNullableAnyOf rewrites every `{"anyOf":[T,{"type":"null"}]}` (in any
// order) into T (preserving sibling keys like `default`/`description`). This is
// exactly how pydantic emits `X | None`; the generator turns the collapsed form
// into a Go pointer. Any other anyOf is left untouched. It does not alter the
// set of documents the schema accepts — a `*X` still marshals to `X`-or-absent,
// which the original `anyOf[X,null]` permits.
func collapseNullableAnyOf(schema []byte) ([]byte, error) {
	var root any
	if err := json.Unmarshal(schema, &root); err != nil {
		return nil, err
	}
	out := walk(root)
	return json.MarshalIndent(out, "", "  ")
}

func walk(node any) any {
	switch n := node.(type) {
	case map[string]any:
		if collapsed, ok := collapseNode(n); ok {
			return walk(collapsed)
		}
		for k, v := range n {
			n[k] = walk(v)
		}
		return n
	case []any:
		for i, v := range n {
			n[i] = walk(v)
		}
		return n
	default:
		return node
	}
}

// collapseNode returns (rewritten, true) when node is a nullable anyOf pair.
func collapseNode(n map[string]any) (map[string]any, bool) {
	raw, ok := n["anyOf"]
	if !ok {
		return nil, false
	}
	arr, ok := raw.([]any)
	if !ok || len(arr) != 2 {
		return nil, false
	}
	var nonNull map[string]any
	sawNull := false
	for _, item := range arr {
		m, ok := item.(map[string]any)
		if !ok {
			return nil, false
		}
		if m["type"] == "null" {
			sawNull = true
			continue
		}
		nonNull = m
	}
	if !sawNull || nonNull == nil {
		return nil, false
	}
	// Merge the non-null branch's keys into the parent (minus anyOf), keeping
	// sibling annotations like default/description/title.
	merged := map[string]any{}
	for k, v := range n {
		if k == "anyOf" {
			continue
		}
		merged[k] = v
	}
	for k, v := range nonNull {
		merged[k] = v
	}
	return merged, true
}

func generate(collapsedSchema, outFile string) error {
	// `go run tool@version` pins the generator; needs only Go on PATH.
	args := []string{
		"run", generatorTool,
		"--only-models",
		"--disable-omitzero",
		"--struct-name-from-title",
		"--capitalization", "ID",
		"--capitalization", "URL",
		"--capitalization", "OK",
		"--tags", "json",
		"-p", "contract",
		"-o", outFile,
		collapsedSchema,
	}
	cmd := exec.Command("go", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	// Allow module-mode `go run` of a tool not in this module's go.mod.
	cmd.Env = append(os.Environ(), "GOFLAGS=-mod=mod")
	return cmd.Run()
}
