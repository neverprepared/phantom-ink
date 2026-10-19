package brainbox

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// sseServer streams n events, then holds the connection open until the test is
// done so the listener does not reconnect mid-assertion.
func sseCountingServer(t *testing.T, n int, onWritten func()) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, ok := w.(http.Flusher)
		if !ok {
			t.Error("test server does not support flushing")
			return
		}
		for i := 0; i < n; i++ {
			fmt.Fprintf(w, "data: {\"n\":%d}\n", i)
			fl.Flush()
		}
		if onWritten != nil {
			onWritten()
		}
		<-r.Context().Done()
	}))
	t.Cleanup(srv.Close)
	return srv
}

// The handler must not run inline in the scan loop. It used to, so the work it
// does (a Wails emit, a full unmarshal, a second emit) backpressured the socket
// read -- which on an SSE stream loses events rather than delaying them.
//
// A slow handler must therefore not stop the reader from draining the socket.
func TestSSEListenerDoesNotBlockReadOnSlowHandler(t *testing.T) {
	writesDone := make(chan struct{})
	var once sync.Once

	srv := sseCountingServer(t, 50, func() { once.Do(func() { close(writesDone) }) })

	var handled atomic.Int64
	release := make(chan struct{})
	l := NewSSEListener(NewClient(srv.URL, ""), func(string) {
		// First call blocks until released: inline dispatch would stall the
		// socket read here and the server could not finish writing.
		if handled.Add(1) == 1 {
			<-release
		}
	})
	l.Start()
	t.Cleanup(l.Stop)

	select {
	case <-writesDone:
		// The server wrote every event while the handler was still blocked,
		// which is only possible if the read loop is independent of it.
	case <-time.After(10 * time.Second):
		close(release)
		t.Fatal("server could not finish writing while the handler was blocked: the read is still coupled to dispatch")
	}
	close(release)
}

// Events must still be delivered in order once the handler keeps up.
func TestSSEListenerDeliversEvents(t *testing.T) {
	srv := sseCountingServer(t, 5, nil)

	var mu sync.Mutex
	var got []string
	done := make(chan struct{})
	var closeOnce sync.Once

	l := NewSSEListener(NewClient(srv.URL, ""), func(data string) {
		mu.Lock()
		got = append(got, data)
		n := len(got)
		mu.Unlock()
		if n == 5 {
			closeOnce.Do(func() { close(done) })
		}
	})
	l.Start()
	t.Cleanup(l.Stop)

	select {
	case <-done:
	case <-time.After(10 * time.Second):
		mu.Lock()
		n := len(got)
		mu.Unlock()
		t.Fatalf("only %d of 5 events delivered", n)
	}

	mu.Lock()
	defer mu.Unlock()
	for i, want := range []string{`{"n":0}`, `{"n":1}`, `{"n":2}`, `{"n":3}`, `{"n":4}`} {
		if got[i] != want {
			t.Errorf("event %d = %q, want %q (order not preserved)", i, got[i], want)
		}
	}
}

// An event larger than bufio's 64KB default must not kill the stream. That cap
// turned one oversized envelope into a reconnect loop that backed off to 30s.
func TestSSEListenerAcceptsLargeEvent(t *testing.T) {
	big := make([]byte, 200*1024)
	for i := range big {
		big[i] = 'x'
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl := w.(http.Flusher)
		fmt.Fprintf(w, "data: {\"big\":\"%s\"}\n", big)
		fl.Flush()
		<-r.Context().Done()
	}))
	t.Cleanup(srv.Close)

	got := make(chan int, 1)
	l := NewSSEListener(NewClient(srv.URL, ""), func(data string) {
		select {
		case got <- len(data):
		default:
		}
	})
	l.Start()
	t.Cleanup(l.Stop)

	select {
	case n := <-got:
		if n < 200*1024 {
			t.Errorf("received %d bytes, want the full >200KB event", n)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("a 200KB event never arrived; the line cap is still dropping the stream")
	}
}
