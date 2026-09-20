package provider

import (
	"errors"
	"net/http"
	"testing"
)

func TestIsUnauthorized(t *testing.T) {
	if !IsUnauthorized(&StatusError{Code: http.StatusUnauthorized, Status: "401", Path: "/x"}) {
		t.Fatal("401 StatusError should be unauthorized")
	}
	if IsUnauthorized(&StatusError{Code: http.StatusNotFound}) {
		t.Fatal("404 is not unauthorized")
	}
	if IsUnauthorized(errors.New("plain")) {
		t.Fatal("non-StatusError is not unauthorized")
	}
}

func TestStatusErrorMessage(t *testing.T) {
	e := &StatusError{Code: 500, Status: "500 Internal Server Error", Path: "/repos"}
	if got := e.Error(); got != "provider /repos: 500 Internal Server Error" {
		t.Fatalf("unexpected message: %q", got)
	}
}
