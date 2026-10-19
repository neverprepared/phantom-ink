package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// boundMethodsAllowedAmbientProfile are the exported *App methods that may
// legitimately fall back to the active profile.
//
// BrowseProfileFiles takes an explicit profileName and only defaults to the
// active profile when it is empty — the right behaviour for a file picker,
// which is a genuinely global operation rather than a per-tenant read or write.
var boundMethodsAllowedAmbientProfile = map[string]bool{
	"BrowseProfileFiles": true,
}

// A bound (exported) *App method must not resolve which profile it acts on by
// calling a.activeProfileName(). The whole App struct is bound, so these are
// callable from the webview at any time: the frontend renders a panel under one
// profile, and the user can switch while a call is in flight. An ambient read
// therefore lands the operation on whichever profile is active when it
// completes, not the one the operator was looking at.
//
// That is how five vault methods came to share the dashboard cross-profile save
// race — including ForgetVaultRecord, which tombstones a record, and
// Move/CopyVaultRecord, which took the destination explicitly while reading the
// source ambiently. Cross-profile leakage is a bug by project rule, so this is
// a gate rather than a convention.
//
// Unexported helpers are free to use it: they are not reachable from the
// frontend, and the caller has already pinned the profile.
func TestNoBoundMethodReadsActiveProfileAmbiently(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatalf("glob: %v", err)
	}

	fset := token.NewFileSet()
	var offenders []string

	for _, path := range files {
		if strings.HasSuffix(path, "_test.go") {
			continue
		}
		src, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		f, err := parser.ParseFile(fset, path, src, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}

		for _, decl := range f.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Recv == nil || fn.Body == nil {
				continue
			}
			if !fn.Name.IsExported() || !isAppReceiver(fn.Recv) {
				continue
			}
			if boundMethodsAllowedAmbientProfile[fn.Name.Name] {
				continue
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				sel, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "activeProfileName" {
					return true
				}
				offenders = append(offenders, fn.Name.Name+" ("+fset.Position(sel.Pos()).String()+")")
				return false
			})
		}
	}

	for _, o := range offenders {
		t.Errorf("bound method resolves the profile ambiently: %s\n"+
			"\ttake the profile as a parameter instead, as the other bound methods do,\n"+
			"\tor add it to boundMethodsAllowedAmbientProfile with a reason.", o)
	}
}

// isAppReceiver reports whether a method's receiver is *App or App.
func isAppReceiver(recv *ast.FieldList) bool {
	if recv == nil || len(recv.List) != 1 {
		return false
	}
	switch typ := recv.List[0].Type.(type) {
	case *ast.StarExpr:
		id, ok := typ.X.(*ast.Ident)
		return ok && id.Name == "App"
	case *ast.Ident:
		return typ.Name == "App"
	}
	return false
}

// window.confirm() is an unreliable no-op in the Wails webview: it returns
// undefined, so the idiomatic `if (!window.confirm(msg)) return;` guard bails
// unconditionally and the guarded action silently never runs — no dialog, no
// error, nothing for the user to retry against.
//
// This was discovered twice and worked around locally both times, while four
// destructive actions elsewhere stayed broken for lack of a gate. Use
// confirmAsk() from lib/confirm.svelte.ts instead.
//
// This lives in the Go suite because the frontend has no test runner, and CI
// already runs `go test`.
func TestFrontendDoesNotUseWindowConfirm(t *testing.T) {
	root := filepath.Join("frontend", "src")
	if _, err := os.Stat(root); err != nil {
		t.Skipf("frontend source not present: %v", err)
	}

	var offenders []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		switch filepath.Ext(path) {
		case ".svelte", ".ts", ".js":
		default:
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for i, line := range strings.Split(string(src), "\n") {
			trimmed := strings.TrimSpace(line)
			// Skip comments — the replacement documents what it replaced.
			if strings.HasPrefix(trimmed, "//") || strings.HasPrefix(trimmed, "*") ||
				strings.HasPrefix(trimmed, "/*") {
				continue
			}
			if strings.Contains(line, "window.confirm(") {
				offenders = append(offenders, fileLine(path, i+1))
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk: %v", err)
	}

	for _, o := range offenders {
		t.Errorf("window.confirm() is a silent no-op in the Wails webview: %s\n"+
			"\tuse confirmAsk() from lib/confirm.svelte.ts instead.", o)
	}
}

func fileLine(path string, line int) string {
	return path + ":" + itoa(line)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
