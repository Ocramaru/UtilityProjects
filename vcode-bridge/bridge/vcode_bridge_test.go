package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/pelletier/go-toml/v2"
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

func TestManagerReloadKeepsOtherHostAndRoutesBySocket(t *testing.T) {
	configPath := shortSocket(t) + ".toml"
	dir := filepath.Dir(configPath)
	ssh := filepath.Join(dir, "fake-ssh")
	cleaned := filepath.Join(dir, "cleaned")
	if err := os.WriteFile(ssh, []byte("#!/bin/sh\ncase \" $* \" in *' -G '*) printf 'user remoteuser\\n';; *'rm -f'*) echo \"$*\" >> '"+cleaned+"';; *) exec sleep 30;; esac\n"), 0700); err != nil {
		t.Fatal(err)
	}
	makeHost := func(name string) (hostConfig, string) {
		output := filepath.Join(dir, name+"-opened")
		code := filepath.Join(dir, name+"-code")
		if err := os.WriteFile(code, []byte("#!/bin/sh\nprintf '%s\\n' \"$@\" > '"+output+"'\n"), 0700); err != nil {
			t.Fatal(err)
		}
		// Every host shares one remote socket name, as the installer writes them: it is named for the Mac.
		return hostConfig{Host: name, Socket: filepath.Join(dir, name+".sock"),
			RemoteSocket: "/tmp/vcode-bridge-remoteuser-testid.sock", SSH: ssh, Code: code,
			Log: filepath.Join(dir, name+".log")}, output
	}
	first, firstOutput := makeHost("host-a")
	second, secondOutput := makeHost("host-b")
	writeConfig := func(hosts ...hostConfig) {
		data, err := toml.Marshal(managedConfig{Hosts: hosts})
		if err != nil {
			t.Fatal(err)
		}
		temporary := configPath + ".new"
		if err := os.WriteFile(temporary, data, 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(temporary, configPath); err != nil {
			t.Fatal(err)
		}
	}
	client := func(socket string) *http.Client {
		return &http.Client{Timeout: time.Second, Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, "unix", socket)
			},
		}}
	}
	waitHealth := func(socket string, wantHealthy bool) {
		t.Helper()
		deadline := time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			response, err := client(socket).Get("http://localhost/health")
			if err == nil {
				response.Body.Close()
			}
			if (err == nil && response.StatusCode == http.StatusNoContent) == wantHealthy {
				return
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatalf("socket %s did not reach healthy=%t", socket, wantHealthy)
	}
	open := func(entry hostConfig, path, output string) {
		t.Helper()
		response, err := client(entry.Socket).Post("http://localhost/open", "text/plain", strings.NewReader(path))
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != http.StatusAccepted {
			t.Fatal(response.Status)
		}
		deadline := time.Now().Add(time.Second)
		for time.Now().Before(deadline) {
			data, err := os.ReadFile(output)
			if err == nil && string(data) == "--remote\nssh-remote+"+entry.Host+"\n"+path+"\n" {
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Fatalf("wrong VS Code destination for %s", entry.Host)
	}

	writeConfig(first)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runManager(ctx, configPath) }()
	defer func() {
		cancel()
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	}()
	waitHealth(first.Socket, true)
	deadline := time.Now().Add(5 * time.Second)
	for {
		data, _ := os.ReadFile(cleaned)
		if strings.Contains(string(data), "rm -f -- '"+first.RemoteSocket+"'") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("stale remote socket was not removed before the tunnel started")
		}
		time.Sleep(20 * time.Millisecond)
	}
	before, err := os.Stat(first.Socket)
	if err != nil {
		t.Fatal(err)
	}
	writeConfig(first, second)
	waitHealth(second.Socket, true)
	open(first, "/projects/first", firstOutput)
	open(second, "/projects/second", secondOutput)
	writeConfig(first)
	waitHealth(second.Socket, false)
	waitHealth(first.Socket, true)
	after, err := os.Stat(first.Socket)
	if err != nil || !os.SameFile(before, after) {
		t.Fatalf("unchanged host was restarted: %v", err)
	}
}
