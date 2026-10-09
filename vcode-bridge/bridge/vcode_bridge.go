// Package main is vcode-bridge, the Mac service that opens remote paths in local VS Code through SSH reverse forwards.
// It keeps one tunnel per configured host and reloads the host list from its config file every second.
// Author: Marco Cassar (@Ocramaru)
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/pelletier/go-toml/v2"
)

const maxPathBytes = 8192

// version is set at build time with -ldflags "-X main.version=..."; a plain go build reports dev.
var version = "dev"

// sshOptions keep the service's connections off shared control sockets and unattended, whatever the user's SSH config says.
var sshOptions = []string{"-S", "none", "-T", "-o", "ControlMaster=no", "-o", "ControlPersist=no",
	"-o", "ForkAfterAuthentication=no", "-o", "ClearAllForwardings=no", "-o", "RemoteCommand=none",
	"-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
	"-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes"}

type hostConfig struct {
	Host         string `toml:"host"`
	Socket       string `toml:"socket"`
	RemoteSocket string `toml:"remote_socket"`
	SSH          string `toml:"ssh"`
	Code         string `toml:"code"`
	Log          string `toml:"log"`
}

type managedConfig struct {
	Hosts []hostConfig `toml:"hosts"`
}

type bridge struct {
	entry  hostConfig
	logger *log.Logger
}

// unixListener replaces a leftover socket file; the manager lock means no other bridge can be serving it.
func unixListener(socketPath string) (net.Listener, error) {
	if info, err := os.Lstat(socketPath); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return nil, fmt.Errorf("refusing to replace non-socket %s", socketPath)
		}
		if err := os.Remove(socketPath); err != nil {
			return nil, fmt.Errorf("removing stale socket: %w", err)
		}
	}
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(socketPath, 0600); err != nil {
		listener.Close()
		return nil, err
	}
	return listener, nil
}

func validRemotePath(raw []byte) (string, bool) {
	if len(raw) == 0 || len(raw) > maxPathBytes || !utf8.Valid(raw) {
		return "", false
	}

	remotePath := string(raw)
	if !strings.HasPrefix(remotePath, "/") {
		return "", false
	}
	for _, character := range remotePath {
		if character < 32 || character == 127 {
			return "", false
		}
	}

	return path.Clean(remotePath), true
}

func (b bridge) health(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	response.WriteHeader(http.StatusNoContent)
}

func (b bridge) open(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if request.Header.Get("Origin") != "" {
		http.Error(response, "browser requests are not allowed", http.StatusForbidden)
		return
	}

	body, err := io.ReadAll(http.MaxBytesReader(response, request.Body, maxPathBytes+1))
	if err != nil || len(body) > maxPathBytes {
		http.Error(response, "invalid path length", http.StatusBadRequest)
		return
	}

	remotePath, valid := validRemotePath(body)
	if !valid {
		http.Error(response, "expected one absolute remote path", http.StatusBadRequest)
		return
	}

	command := exec.Command(b.entry.Code, "--remote", "ssh-remote+"+b.entry.Host, remotePath)
	if err := command.Start(); err != nil {
		http.Error(response, "could not start VS Code", http.StatusInternalServerError)
		return
	}
	go func() {
		if err := command.Wait(); err != nil {
			b.logger.Printf("VS Code command exited with an error: %v", err)
		}
	}()

	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(http.StatusAccepted)
	fmt.Fprintf(response, "Opening %s in VS Code\n", remotePath)
}

// bridgeLock is held for the manager's whole life, SSH child shutdown included.
// The lock file is never unlinked: replacing its inode would allow two owners.
func bridgeLock(lockedPath string) (*os.File, error) {
	lock, err := os.OpenFile(lockedPath+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("another bridge service owns %s: %w", lockedPath, err)
	}
	return lock, nil
}

// checkSSHConfig rejects forwarding directives, which would ride along on the service's tunnel.
func checkSSHConfig(ctx context.Context, entry hostConfig) error {
	output, err := exec.CommandContext(ctx, entry.SSH, slices.Concat(sshOptions, []string{"-G", entry.Host})...).Output()
	if err != nil {
		return fmt.Errorf("reading effective SSH configuration: %w", err)
	}
	for _, line := range strings.Split(string(output), "\n") {
		key, value, _ := strings.Cut(line, " ")
		switch key {
		case "remoteforward", "localforward", "dynamicforward":
			return fmt.Errorf("remove %s %s from Host %s; the bridge owns its forwarding", key, value, entry.Host)
		}
	}
	return nil
}

// runTunnel deletes this Mac's stale remote socket, whose name carries the Mac's id, then holds the reverse forward open.
func runTunnel(ctx context.Context, entry hostConfig, output io.Writer) error {
	removeStale := "rm -f -- '" + entry.RemoteSocket + "'"
	cleanup := exec.CommandContext(ctx, entry.SSH, slices.Concat(sshOptions, []string{entry.Host, removeStale})...)
	cleanup.Stdout, cleanup.Stderr = output, output
	if err := cleanup.Run(); err != nil {
		return fmt.Errorf("removing stale remote socket: %w", err)
	}
	forward := entry.RemoteSocket + ":" + entry.Socket
	tunnel := exec.CommandContext(ctx, entry.SSH, slices.Concat(sshOptions, []string{"-N", "-R", forward, entry.Host})...)
	tunnel.Stdout, tunnel.Stderr = output, output
	return tunnel.Run()
}

// superviseTunnel keeps exactly one SSH child per host, waiting for each to exit before starting the next.
func superviseTunnel(ctx context.Context, entry hostConfig, output io.Writer, logger *log.Logger) {
	const (
		minimumDelay = time.Second
		maximumDelay = 30 * time.Second
	)
	delay := minimumDelay
	for ctx.Err() == nil {
		started := time.Now()
		err := runTunnel(ctx, entry, output)
		if ctx.Err() != nil {
			return
		}
		if time.Since(started) >= time.Minute {
			delay = minimumDelay
		}
		logger.Printf("bridge SSH connection ended (%v); reconnecting in %s", err, delay)
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
		delay = min(delay*2, maximumDelay)
	}
}

func validateHost(entry hostConfig) error {
	if entry.Host == "" || strings.HasPrefix(entry.Host, "-") || strings.ContainsAny(entry.Host, "/:\\ \t\r\n") {
		return fmt.Errorf("invalid SSH host alias %q", entry.Host)
	}
	if !filepath.IsAbs(entry.Socket) || len(entry.Socket) >= 104 {
		return fmt.Errorf("invalid local socket for %s", entry.Host)
	}
	if !path.IsAbs(entry.RemoteSocket) || strings.ContainsAny(entry.RemoteSocket, "\x00\r\n: '") || len(entry.RemoteSocket) > 100 {
		return fmt.Errorf("invalid remote socket for %s", entry.Host)
	}
	for _, executable := range []string{entry.SSH, entry.Code} {
		info, err := os.Stat(executable)
		if err != nil || info.IsDir() || info.Mode()&0111 == 0 {
			return fmt.Errorf("command is not executable: %s", executable)
		}
	}
	return nil
}

func serveHost(ctx context.Context, entry hostConfig, output io.Writer) error {
	checkCtx, cancelCheck := context.WithTimeout(ctx, 10*time.Second)
	err := checkSSHConfig(checkCtx, entry)
	cancelCheck()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(entry.Socket), 0700); err != nil {
		return err
	}
	listener, err := unixListener(entry.Socket)
	if err != nil {
		return err
	}
	defer listener.Close()

	logger := log.New(output, entry.Host+": ", log.LstdFlags)
	handler := bridge{entry: entry, logger: logger}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", handler.health)
	mux.HandleFunc("/open", handler.open)
	server := &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 2 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       30 * time.Second,
	}

	logger.Printf("listening on %s", entry.Socket)
	tunnelCtx, cancelTunnel := context.WithCancel(ctx)
	tunnelDone := make(chan struct{})
	go func() {
		defer close(tunnelDone)
		superviseTunnel(tunnelCtx, entry, output, logger)
	}()
	defer func() { cancelTunnel(); <-tunnelDone }()
	go func() { <-tunnelCtx.Done(); server.Close() }()
	if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func readManagedConfig(configPath string) ([]hostConfig, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, err
	}
	var config managedConfig
	if err := toml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", configPath, err)
	}
	// Remote sockets repeat across hosts by design: each is named for this Mac, not for the host.
	hosts, sockets := map[string]bool{}, map[string]bool{}
	for _, entry := range config.Hosts {
		if err := validateHost(entry); err != nil {
			return nil, err
		}
		if hosts[entry.Host] || sockets[entry.Socket] {
			return nil, fmt.Errorf("duplicate host or local socket for %s", entry.Host)
		}
		hosts[entry.Host], sockets[entry.Socket] = true, true
	}
	return config.Hosts, nil
}

type activeHost struct {
	entry  hostConfig
	cancel context.CancelFunc
	done   chan struct{}
}

// runHostLoop restarts a host's bridge after any failure, writing to the host's own log.
func runHostLoop(ctx context.Context, entry hostConfig) {
	for ctx.Err() == nil {
		output := io.Writer(os.Stdout)
		logFile, err := os.OpenFile(entry.Log, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			log.Printf("%s: opening log: %v", entry.Host, err)
		} else {
			output = logFile
		}
		err = serveHost(ctx, entry, output)
		if logFile != nil {
			logFile.Close()
		}
		if ctx.Err() != nil {
			return
		}
		log.Printf("%s: bridge stopped (%v); retrying in 5s", entry.Host, err)
		select {
		case <-ctx.Done():
			return
		case <-time.After(5 * time.Second):
		}
	}
}

// reconcileHosts stops hosts that were removed or changed and starts new ones, leaving the rest running.
func reconcileHosts(ctx context.Context, active map[string]activeHost, desired []hostConfig) {
	wanted := make(map[string]hostConfig, len(desired))
	for _, entry := range desired {
		wanted[entry.Host] = entry
	}
	for host, current := range active {
		if next, found := wanted[host]; !found || next != current.entry {
			current.cancel()
			<-current.done
			delete(active, host)
			log.Printf("stopped bridge for %s", host)
		}
	}
	for _, entry := range desired {
		if _, found := active[entry.Host]; found {
			continue
		}
		hostCtx, cancel := context.WithCancel(ctx)
		done := make(chan struct{})
		active[entry.Host] = activeHost{entry: entry, cancel: cancel, done: done}
		go func() {
			defer close(done)
			runHostLoop(hostCtx, entry)
		}()
		log.Printf("started bridge for %s", entry.Host)
	}
}

func runManager(ctx context.Context, configPath string) error {
	if !filepath.IsAbs(configPath) {
		return fmt.Errorf("-config must be an absolute path")
	}
	lock, err := bridgeLock(configPath)
	if err != nil {
		return err
	}
	defer lock.Close()
	active := make(map[string]activeHost)
	defer func() {
		for _, current := range active {
			current.cancel()
		}
		for _, current := range active {
			<-current.done
		}
	}()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	lastError := ""
	for {
		desired, err := readManagedConfig(configPath)
		if err == nil {
			lastError = ""
			reconcileHosts(ctx, active, desired)
		} else if err.Error() != lastError {
			lastError = err.Error()
			log.Printf("cannot load bridge configuration: %v", err)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func run() error {
	configPath := flag.String("config", "", "host configuration file")
	checkConfig := flag.Bool("check-config", false, "validate the managed host configuration and exit")
	showVersion := flag.Bool("version", false, "print the version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println("vcode-bridge", version)
		return nil
	}
	if *configPath == "" {
		return fmt.Errorf("-config is required")
	}
	if *checkConfig {
		_, err := readManagedConfig(*configPath)
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return runManager(ctx, *configPath)
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
