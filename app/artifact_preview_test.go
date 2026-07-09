package main

import "testing"

func TestClassifyPreview(t *testing.T) {
	cases := []struct {
		name        string
		contentType string
		filename    string
		size        int64
		wantKind    string
	}{
		{"markdown by ext", "", "notes/readme.md", 100, "text"},
		{"json by type", "application/json", "x", 100, "text"},
		{"text type", "text/plain; charset=utf-8", "x.bin", 100, "text"},
		{"octet-stream with texty ext", "application/octet-stream", "run.log", 100, "text"},
		{"octet-stream binary", "application/octet-stream", "blob.dat", 100, "unsupported"},
		{"png", "image/png", "shot.png", 100, "image"},
		{"svg", "image/svg+xml", "icon.svg", 100, "image"},
		{"oversized text", "text/plain", "big.txt", previewTextCap + 1, "unsupported"},
		{"oversized image", "image/png", "big.png", previewImageCap + 1, "unsupported"},
		{"binary", "application/zip", "a.zip", 100, "unsupported"},
		{"yaml by type", "application/x-yaml", "cfg", 100, "text"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			kind, _ := classifyPreview(c.contentType, c.filename, c.size)
			if kind != c.wantKind {
				t.Errorf("classifyPreview(%q, %q, %d) = %q, want %q",
					c.contentType, c.filename, c.size, kind, c.wantKind)
			}
		})
	}
}
