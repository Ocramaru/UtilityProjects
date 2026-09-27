package main

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func shortSocket(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "vc-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return filepath.Join(dir, "bridge.sock")
}

func TestLockRejectsDuplicateAndAllowsRestart(t *testing.T) {
	socket := shortSocket(t)
	first, err := bridgeLock(socket)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := bridgeLock(socket); err == nil {
		second.Close()
		t.Fatal("duplicate service acquired lock")
	}
	first.Close()
	restarted, err := bridgeLock(socket)
	if err != nil {
		t.Fatal(err)
	}
	restarted.Close()
}

func TestListenerPreservesLiveSocket(t *testing.T) {
	socket := shortSocket(t)
	first, err := unixListener(socket)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := unixListener(socket); err == nil {
		second.Close()
		t.Fatal("replaced live socket")
	}
	conn, err := net.Dial("unix", socket)
	if err != nil {
		t.Fatalf("original listener lost: %v", err)
	}
	conn.Close()
}

func TestListenerRecoversStaleSocket(t *testing.T) {
	socket := shortSocket(t)
	stale, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	stale.SetUnlinkOnClose(false)
	stale.Close()
	listener, err := unixListener(socket)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	info, err := os.Stat(socket)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("socket permissions: %v %v", info, err)
	}
}

func TestListenerPreservesRegularFile(t *testing.T) {
	socket := shortSocket(t)
	os.WriteFile(socket, []byte("keep"), 0600)
	if listener, err := unixListener(socket); err == nil {
		listener.Close()
		t.Fatal("replaced regular file")
	}
	data, _ := os.ReadFile(socket)
	if string(data) != "keep" {
		t.Fatal("file was changed")
	}
}

func TestSupervisorReconnectsAndStops(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	attempts := make(chan int, 3)
	done := make(chan struct{})
	go func() {
		defer close(done)
		count := 0
		superviseTunnel(ctx, func(ctx context.Context) error {
			count++
			attempts <- count
			if count == 1 {
				return syscall.ECONNRESET
			}
			<-ctx.Done()
			return ctx.Err()
		}, time.Millisecond, 5*time.Millisecond)
	}()
	for i := 1; i <= 2; i++ {
		select {
		case got := <-attempts:
			if got != i {
				t.Fatal(got)
			}
		case <-time.After(time.Second):
			t.Fatal("did not reconnect")
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("supervisor did not stop")
	}
}

func TestEffectiveConfigRejectsLegacyForwardAndResolvesUser(t *testing.T) {
	dir := filepath.Dir(shortSocket(t))
	fake := filepath.Join(dir, "ssh")
	for _, tt := range []struct {
		output    string
		wantError bool
	}{
		{"user marcocassar\nhostname sparky\n", false},
		{"user marcocassar\nremoteforward /tmp/old.sock /tmp/local.sock\n", true},
	} {
		script := "#!/bin/sh\nprintf '%s\\n' '" + tt.output + "'\n"
		if err := os.WriteFile(fake, []byte(script), 0700); err != nil {
			t.Fatal(err)
		}
		socket, err := remoteSocketForHost(context.Background(), fake, "sparky")
		if (err != nil) != tt.wantError {
			t.Fatalf("socket=%q err=%v", socket, err)
		}
		if err == nil && socket != "/tmp/vcode-bridge-marcocassar.sock" {
			t.Fatal(socket)
		}
	}
}

func TestRemotePathValidation(t *testing.T) {
	for _, input := range []string{"", "relative", "/path\ncommand", string([]byte{'/', 0xff}), "/" + strings.Repeat("x", maxPathBytes)} {
		if _, valid := validRemotePath([]byte(input)); valid {
			t.Fatalf("accepted %q", input)
		}
	}
	if got, valid := validRemotePath([]byte("/a folder/../another")); !valid || got != "/another" {
		t.Fatal(got, valid)
	}
}
