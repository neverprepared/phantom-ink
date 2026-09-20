package main

import (
	"net/http"
	"net/http/httptest"
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
		st := checkADOConnection(srv.URL, "acme", "widgets", "pat", &http.Client{Timeout: 5 * time.Second})
		if !st.Valid || !st.Checked {
			t.Fatalf("want valid+checked, got %+v", st)
		}
	})

	t.Run("bad pat", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusUnauthorized)
		}))
		defer srv.Close()
		st := checkADOConnection(srv.URL, "acme", "widgets", "bad", &http.Client{Timeout: 5 * time.Second})
		if st.Valid || !st.Checked {
			t.Fatalf("want invalid+checked, got %+v", st)
		}
	})

	t.Run("missing fields", func(t *testing.T) {
		st := checkADOConnection("http://unused", "", "widgets", "pat", http.DefaultClient)
		if st.Valid || !st.Checked {
			t.Fatalf("missing org must be a clean invalid verdict, got %+v", st)
		}
	})
}
