package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func shortDirectory(t *testing.T) string {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "vc-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(directory) })
	return directory
}

func waitForFile(t *testing.T, file, want string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if data, err := os.ReadFile(file); err == nil && string(data) == want {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	data, _ := os.ReadFile(file)
	t.Fatalf("%s holds %q, want %q", file, data, want)
}

func appendString(data, value []byte) []byte {
	return append(binary.BigEndian.AppendUint32(data, uint32(len(value))), value...)
}

func bindMessage(hostKey []byte) []byte {
	message := appendString([]byte{agentExtension}, []byte(sessionBind))
	message = appendString(message, hostKey)
	message = appendString(message, []byte("session identifier"))
	message = appendString(message, []byte("signature"))
	return append(message, 1)
}

// exchange plays a host on the agent socket: ssh's bind messages first, then one HTTP request.
func exchange(t *testing.T, s *server, hostKeys [][]byte, request string) (*http.Response, string) {
	t.Helper()
	client, serverSide := net.Pipe()
	defer client.Close()
	go s.handleConnection(serverSide)
	client.SetDeadline(time.Now().Add(5 * time.Second))
	for _, key := range hostKeys {
		if err := writeAgentMessage(client, bindMessage(key)); err != nil {
			t.Fatal(err)
		}
		if reply, err := readAgentMessage(client); err != nil || !bytes.Equal(reply, []byte{agentSuccess}) {
			t.Fatalf("bind answered %v, %v", reply, err)
		}
	}
	if _, err := client.Write([]byte(request)); err != nil {
		t.Fatal(err)
	}
	response, err := http.ReadResponse(bufio.NewReader(client), nil)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	return response, string(body)
}

func get(path, headers string) string {
	return "GET " + path + " HTTP/1.1\r\nHost: localhost\r\n" + headers + "\r\n"
}

func post(path string) string {
	return fmt.Sprintf("POST /open HTTP/1.1\r\nHost: localhost\r\nContent-Length: %d\r\n\r\n%s", len(path), path)
}

var sparkyKey, rubikKey = []byte("sparky host key"), []byte("rubik host key")

// agentServer has two installed hosts, sparky and rubik, and a VS Code stand-in that records each call in opened.
func agentServer(t *testing.T) (*server, string, string) {
	t.Helper()
	directory := shortDirectory(t)
	code, opened, script := filepath.Join(directory, "code"), filepath.Join(directory, "opened"), filepath.Join(directory, "vcode")
	if err := os.WriteFile(code, []byte("#!/bin/sh\nprintf '%s\\n' \"$@\" >> '"+opened+"'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(script, []byte("#!/usr/bin/env bash\nversion=\"0.2.0\"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	config, _ := json.Marshal(managedConfig{Vcode: script, Code: code, Hosts: []hostConfig{
		{Host: "sparky", HostKeys: []string{base64.StdEncoding.EncodeToString(sparkyKey), "c3Bhcmt5IHJzYSBrZXk="}},
		{Host: "rubik", HostKeys: []string{base64.StdEncoding.EncodeToString(rubikKey)}},
	}})
	configPath := filepath.Join(directory, "config.json")
	if err := os.WriteFile(configPath, config, 0600); err != nil {
		t.Fatal(err)
	}
	return &server{configPath: configPath, logger: log.New(io.Discard, "", 0), lastOpen: map[string]time.Time{}}, opened, script
}

func TestRemotePathValidation(t *testing.T) {
	for _, input := range []string{"", "relative", "--help", "/path\ncommand", string([]byte{'/', 0xff}), "/" + strings.Repeat("x", maxPathBytes)} {
		if _, valid := validRemotePath([]byte(input)); valid {
			t.Fatalf("accepted %q", input)
		}
	}
	for input, want := range map[string]string{"/a folder/../another": "/another", "/a folder/./x/": "/a folder/x/", "/": "/"} {
		if got, valid := validRemotePath([]byte(input)); !valid || got != want {
			t.Fatalf("%q gave %q, %v", input, got, valid)
		}
	}
}

func TestOpensOnTheHostTheSessionIsBoundTo(t *testing.T) {
	s, opened, script := agentServer(t)
	response, body := exchange(t, s, [][]byte{sparkyKey}, post("/home/me/my.site/"))
	if response.StatusCode != http.StatusAccepted || body != "Opening /home/me/my.site/ on sparky in VS Code\n" || response.Header.Get(versionHeader) != version {
		t.Fatalf("%s %q, version %q", response.Status, body, response.Header.Get(versionHeader))
	}
	// Reached through sparky with ssh -A: ssh binds sparky first, then the inner hop, which is where the request comes from.
	if response, _ := exchange(t, s, [][]byte{sparkyKey, rubikKey}, post("/data/notes file")); response.StatusCode != http.StatusAccepted {
		t.Fatal(response.Status)
	}
	waitForFile(t, opened, "--folder-uri\nvscode-remote://ssh-remote+sparky/home/me/my.site/\n--file-uri\nvscode-remote://ssh-remote+rubik/data/notes%20file\n")

	if response, body := exchange(t, s, [][]byte{sparkyKey}, get("/vcode", "")); response.StatusCode != http.StatusOK || body != "#!/usr/bin/env bash\nversion=\"0.2.0\"\n" {
		t.Fatalf("vcode from %s: %s %q", script, response.Status, body)
	}
	if response, body := exchange(t, s, [][]byte{[]byte("new host key")}, get("/probe", "")); body != base64.StdEncoding.EncodeToString([]byte("new host key"))+"\n" {
		t.Fatalf("probe: %s %q", response.Status, body)
	}
}

func TestRefusals(t *testing.T) {
	s, opened, _ := agentServer(t)
	rubikKeys := hostKeysHeader + ": " + base64.StdEncoding.EncodeToString(rubikKey) + " c29tZSBvdGhlcg==\r\n"
	for _, test := range []struct {
		name, request, reason string
		binds                 [][]byte
	}{
		{"a host that is not installed", get("/health", ""), "not installed on the Mac", [][]byte{[]byte("some other host key")}},
		{"a process on the Mac", post("/work"), "only SSH hosts", nil},
		// vcode on rubik sends rubik's own keys, but an old ssh on sparky forwarded without binding, so only sparky's key arrived.
		{"a hop that did not bind", get("/health", rubikKeys), "OpenSSH 8.9", [][]byte{sparkyKey}},
		{"a probe through a hop", get("/probe", ""), "straight from the Mac", [][]byte{sparkyKey, rubikKey}},
	} {
		if response, body := exchange(t, s, test.binds, test.request); response.StatusCode != http.StatusForbidden || !strings.Contains(body, test.reason) {
			t.Errorf("%s got %s %q", test.name, response.Status, body)
		}
	}
	time.Sleep(100 * time.Millisecond)
	if _, err := os.Stat(opened); err == nil {
		t.Fatal("VS Code was started")
	}
}

func TestKeyRequestsGetNoKeys(t *testing.T) {
	s, _, _ := agentServer(t)
	client, serverSide := net.Pipe()
	defer client.Close()
	go s.handleConnection(serverSide)
	client.SetDeadline(time.Now().Add(5 * time.Second))
	if err := writeAgentMessage(client, []byte{agentRequestIdentities}); err != nil {
		t.Fatal(err)
	}
	reply, err := readAgentMessage(client)
	if err != nil || !bytes.Equal(reply, []byte{agentIdentitiesAnswer, 0, 0, 0, 0}) {
		t.Fatalf("identities answered %v, %v", reply, err)
	}
}

func TestOpensAreLimitedToOneASecond(t *testing.T) {
	s, _, _ := agentServer(t)
	if first, _ := exchange(t, s, [][]byte{sparkyKey}, post("/one")); first.StatusCode != http.StatusAccepted {
		t.Fatal(first.Status)
	}
	if second, _ := exchange(t, s, [][]byte{sparkyKey}, post("/two")); second.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("second open within a second: %s", second.Status)
	}
}

func TestTheServiceAnswersBeforeAnyHostIsInstalledAndHoldsALock(t *testing.T) {
	directory := shortDirectory(t)
	configPath, socketPath := filepath.Join(directory, "config.json"), filepath.Join(directory, "agent.sock")
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runService(ctx, configPath, socketPath) }()
	defer func() {
		cancel()
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	}()

	client := &http.Client{Timeout: time.Second, Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		},
	}}
	deadline := time.Now().Add(2 * time.Second)
	for {
		response, err := client.Get("http://localhost/health")
		if err == nil && response.StatusCode == http.StatusNoContent {
			response.Body.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the service never answered: %v", err)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := runService(context.Background(), configPath, socketPath); err == nil || !strings.Contains(err.Error(), "another bridge service") {
		t.Fatalf("a second service started: %v", err)
	}
}
