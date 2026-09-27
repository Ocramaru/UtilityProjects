// vcode-bridge opens validated remote paths in local VS Code through an SSH reverse forward.
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
	"strings"
	"syscall"
	"time"
	"unicode/utf8"
)

const maxPathBytes = 8192

type bridge struct {
	sshHost  string
	codePath string
}

func unixListener(socketPath string) (net.Listener, error) {
	info, err := os.Lstat(socketPath)
	if err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return nil, fmt.Errorf("refusing to replace non-socket path")
		}
		connection, dialErr := net.DialTimeout("unix", socketPath, time.Second)
		if dialErr == nil {
			connection.Close()
			return nil, fmt.Errorf("another bridge is already listening on %s", socketPath)
		}
		if !errors.Is(dialErr, syscall.ECONNREFUSED) && !errors.Is(dialErr, os.ErrNotExist) {
			return nil, fmt.Errorf("cannot establish whether socket is stale: %w", dialErr)
		}
		if err := os.Remove(socketPath); err != nil && !os.IsNotExist(err) {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}

	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(socketPath, 0600); err != nil {
		listener.Close()
		os.Remove(socketPath)
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

	command := exec.Command(b.codePath, "--remote", "ssh-remote+"+b.sshHost, remotePath)
	command.Stdin = nil
	command.Stdout = io.Discard
	command.Stderr = io.Discard
	if err := command.Start(); err != nil {
		http.Error(response, "could not start VS Code", http.StatusInternalServerError)
		return
	}
	go func() {
		if err := command.Wait(); err != nil {
			log.Printf("VS Code command exited with an error: %v", err)
		}
	}()

	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(http.StatusAccepted)
	fmt.Fprintf(response, "Opening %s in VS Code\n", remotePath)
}

// Hold the lock for the entire service lifetime, including SSH child shutdown.
// Never unlink the lock file: replacing its inode would allow two owners.
func bridgeLock(socketPath string) (*os.File, error) {
	lock, err := os.OpenFile(socketPath+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("another bridge service owns this socket: %w", err)
	}
	return lock, nil
}

func independentSSHArgs() []string {
	return []string{"-S", "none", "-T", "-o", "ControlMaster=no", "-o", "ControlPersist=no",
		"-o", "ForkAfterAuthentication=no", "-o", "ClearAllForwardings=no", "-o", "RemoteCommand=none",
		"-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
		"-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes"}
}

// Resolve %r using SSH itself, including User/HostName/Include/Match configuration.
// Legacy per-session forwards must be removed, otherwise SSH requests them too.
func remoteSocketForHost(ctx context.Context, sshPath, host string) (string, error) {
	args := append(independentSSHArgs(), "-G", host)
	output, err := exec.CommandContext(ctx, sshPath, args...).Output()
	if err != nil {
		return "", fmt.Errorf("read effective SSH configuration: %w", err)
	}
	remoteUser := ""
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		switch fields[0] {
		case "user":
			remoteUser = fields[1]
		case "remoteforward", "localforward", "dynamicforward":
			return "", fmt.Errorf("remove %s from Host %s; the bridge service owns its forwarding", fields[0], host)
		}
	}
	if remoteUser == "" || strings.ContainsAny(remoteUser, "/:\\ \t\r\n") {
		return "", fmt.Errorf("invalid remote SSH username")
	}
	return "/tmp/vcode-bridge-" + remoteUser + ".sock", nil
}

func tunnelArgs(host, remoteSocket, localSocket string) []string {
	return append(independentSSHArgs(), "-N", "-R", remoteSocket+":"+localSocket, host)
}

// The service owns exactly one SSH child. Shell sessions never own this tunnel.
// Killing and waiting for the child before retrying avoids duplicate listeners.
func superviseTunnel(ctx context.Context, run func(context.Context) error, minDelay, maxDelay time.Duration) {
	delay := minDelay
	for ctx.Err() == nil {
		started := time.Now()
		err := run(ctx)
		if ctx.Err() != nil {
			return
		}
		if time.Since(started) >= time.Minute {
			delay = minDelay
		}
		log.Printf("bridge SSH connection ended (%v); reconnecting in %s", err, delay)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		delay *= 2
		if delay > maxDelay {
			delay = maxDelay
		}
	}
}

func run() error {
	sshHost := flag.String("ssh-host", "", "SSH host alias used by VS Code")
	socketPath := flag.String("socket", "", "absolute path to the local Unix socket")
	codePath := flag.String("code", "/usr/local/bin/code", "absolute path to the VS Code CLI")
	sshPath := flag.String("ssh", "/usr/bin/ssh", "absolute path to the SSH client")
	flag.Parse()

	if *sshHost == "" || strings.HasPrefix(*sshHost, "-") {
		return fmt.Errorf("-ssh-host must be a host alias")
	}
	if *socketPath == "" || !filepath.IsAbs(*socketPath) {
		return fmt.Errorf("-socket must be an absolute path")
	}
	codeInfo, err := os.Stat(*codePath)
	if err != nil || codeInfo.IsDir() || codeInfo.Mode()&0111 == 0 {
		return fmt.Errorf("VS Code command is not executable: %s", *codePath)
	}
	if err := os.MkdirAll(filepath.Dir(*socketPath), 0700); err != nil {
		return err
	}
	lock, err := bridgeLock(*socketPath)
	if err != nil {
		return err
	}
	defer lock.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	configCtx, configCancel := context.WithTimeout(ctx, 10*time.Second)
	remoteSocket, err := remoteSocketForHost(configCtx, *sshPath, *sshHost)
	configCancel()
	if err != nil {
		return err
	}
	listener, err := unixListener(*socketPath)
	if err != nil {
		return err
	}
	defer listener.Close()

	handler := bridge{sshHost: *sshHost, codePath: *codePath}
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

	log.Printf("vcode bridge listening on %s for SSH host %s", *socketPath, *sshHost)
	tunnelCtx, cancelTunnel := context.WithCancel(ctx)
	tunnelDone := make(chan struct{})
	go func() {
		defer close(tunnelDone)
		superviseTunnel(tunnelCtx, func(childCtx context.Context) error {
			command := exec.CommandContext(childCtx, *sshPath, tunnelArgs(*sshHost, remoteSocket, *socketPath)...)
			command.Stdout, command.Stderr = os.Stdout, os.Stderr
			return command.Run()
		}, time.Second, 30*time.Second)
	}()
	defer func() { cancelTunnel(); <-tunnelDone }()
	go func() { <-tunnelCtx.Done(); server.Close() }()
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		return err
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
