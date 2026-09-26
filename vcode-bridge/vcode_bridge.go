// vcode-bridge opens validated remote paths in local VS Code through an SSH reverse forward.
// Author: Marco Cassar (@Ocramaru)
package main

import ("flag"; "fmt"; "io"; "log"; "net"; "net/http"; "os"; "os/exec"; "path";
        "path/filepath"; "strings"; "time"; "unicode/utf8")

const maxPathBytes = 8192

type bridge struct {
  sshHost  string
  codePath string
}

func unixListener(socketPath string) (net.Listener, error) {
  info, err := os.Lstat(socketPath)
  if err == nil {
    if info.Mode()&os.ModeSocket == 0 { return nil, fmt.Errorf("refusing to replace non-socket path") }
    if err := os.Remove(socketPath); err != nil { return nil, err }
  } else if !os.IsNotExist(err) { return nil, err }

  listener, err := net.Listen("unix", socketPath)
  if err != nil { return nil, err }
  if err := os.Chmod(socketPath, 0600); err != nil {
    listener.Close()
    os.Remove(socketPath)
    return nil, err
  }
  return listener, nil
}

func validRemotePath(raw []byte) (string, bool) {
  if len(raw) == 0 || len(raw) > maxPathBytes || !utf8.Valid(raw) { return "", false }

  remotePath := string(raw)
  if !strings.HasPrefix(remotePath, "/") { return "", false }
  for _, character := range remotePath {
    if character < 32 || character == 127 { return "", false }
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

func main() {
  sshHost := flag.String("ssh-host", "", "SSH host alias used by VS Code")
  socketPath := flag.String("socket", "", "absolute path to the local Unix socket")
  codePath := flag.String("code", "/usr/local/bin/code", "absolute path to the VS Code CLI")
  flag.Parse()

  if *sshHost == "" { log.Fatal("-ssh-host is required") }
  if *socketPath == "" || !filepath.IsAbs(*socketPath) { log.Fatal("-socket must be an absolute path") }
  codeInfo, err := os.Stat(*codePath)
  if err != nil || codeInfo.IsDir() || codeInfo.Mode()&0111 == 0 {
    log.Fatalf("VS Code command is not executable: %s", *codePath)
  }
  if err := os.MkdirAll(filepath.Dir(*socketPath), 0700); err != nil { log.Fatal(err) }
  listener, err := unixListener(*socketPath)
  if err != nil { log.Fatal(err) }
  defer listener.Close()
  defer os.Remove(*socketPath)

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
  if err := server.Serve(listener); err != nil && err != http.ErrServerClosed { log.Fatal(err) }
}
