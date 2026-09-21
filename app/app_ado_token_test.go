package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCheckADOConnection(t *testing.T) {
	t.Run("valid", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// connectionData and the repositories probe both 200.
			_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"x"},"value":[]}`))
		}))
		defer srv.Close()
		st := checkADOConnection(srv.URL, "acme", "widgets", "pat", "", &http.Client{Timeout: 5 * time.Second})
		if !st.Valid || !st.Checked {
			t.Fatalf("want valid+checked, got %+v", st)
		}
	})

	t.Run("bad pat", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusUnauthorized)
		}))
		defer srv.Close()
		st := checkADOConnection(srv.URL, "acme", "widgets", "bad", "", &http.Client{Timeout: 5 * time.Second})
		if st.Valid || !st.Checked {
			t.Fatalf("want invalid+checked, got %+v", st)
		}
	})

	t.Run("missing fields", func(t *testing.T) {
		st := checkADOConnection("http://unused", "", "widgets", "pat", "", http.DefaultClient)
		if st.Valid || !st.Checked {
			t.Fatalf("missing org must be a clean invalid verdict, got %+v", st)
		}
	})
}

func TestCheckADOConnection_AzLogin(t *testing.T) {
	// No PAT: validation must use the az bearer path the client will use.
	orig := adoAzAuthHeader
	adoAzAuthHeader = func(context.Context, string) (string, error) { return "Bearer az-tok", nil }
	defer func() { adoAzAuthHeader = orig }()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer az-tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"value":[]}`))
	}))
	defer srv.Close()

	st := checkADOConnection(srv.URL, "acme", "widgets", "", "", &http.Client{Timeout: 5 * time.Second})
	if !st.Valid || !st.Checked {
		t.Fatalf("want valid via az login, got %+v", st)
	}
}

func TestCheckADOConnection_AzUnavailable(t *testing.T) {
	orig := adoAzAuthHeader
	adoAzAuthHeader = func(context.Context, string) (string, error) { return "", errors.New("az login required") }
	defer func() { adoAzAuthHeader = orig }()

	st := checkADOConnection("http://unused.invalid", "acme", "widgets", "", "", &http.Client{Timeout: 5 * time.Second})
	if st.Valid || st.Checked {
		t.Fatalf("az unavailable must be inconclusive (Checked=false), got %+v", st)
	}
	if !strings.Contains(st.Message, "az login") {
		t.Fatalf("message should point at az login, got %q", st.Message)
	}
}
