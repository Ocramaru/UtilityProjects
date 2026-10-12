// Package main is vcode-bridge, the Mac service that opens folders from SSH hosts in local VS Code.
// A host reaches it through its own SSH session's agent channel: ForwardAgent points at the service's socket,
// and ssh binds each forwarded connection to the host key it verified, which says which host is asking.
// Author: Marco Cassar (@Ocramaru)
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"
)

const (
	maxPathBytes    = 8192
	maxAgentMessage = 16 * 1024
	maxRequestBytes = 64 * 1024
	maxConnections  = 32
	versionHeader   = "X-Vcode-Bridge-Version"
	hostKeysHeader  = "X-Vcode-Host-Keys"
	sessionBind     = "session-bind@openssh.com"

	agentFailure           = 5
	agentSuccess           = 6
	agentRequestIdentities = 11
	agentIdentitiesAnswer  = 12
	agentExtension         = 27
)

// version is set at build time with -ldflags "-X main.version=..."; a plain go build reports dev.
var version = "dev"

type hostConfig struct {
	Host     string   `json:"host"`
	HostKeys []string `json:"host_keys"`
}

type managedConfig struct {
	Vcode string       `json:"vcode"`
	Code  string       `json:"code"`
	Hosts []hostConfig `json:"hosts"`
}

// server answers requests from hosts, reading the installed config on each one so the installer never has to signal it.
type server struct {
	configPath string
	logger     *log.Logger

	mutex    sync.Mutex
	lastOpen map[string]time.Time
}

// unixListener replaces a leftover socket file; the service lock means no other bridge can be serving it.
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

// validRemotePath cleans an absolute path, keeping the trailing slash that marks a folder.
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

	cleaned := path.Clean(remotePath)
	if strings.HasSuffix(remotePath, "/") && cleaned != "/" {
		cleaned += "/"
	}
	return cleaned, true
}

func readAgentMessage(reader io.Reader) ([]byte, error) {
	var length uint32
	if err := binary.Read(reader, binary.BigEndian, &length); err != nil {
		return nil, err
	}
	if length == 0 || length > maxAgentMessage {
		return nil, fmt.Errorf("agent message of %d bytes", length)
	}
	message := make([]byte, length)
	_, err := io.ReadFull(reader, message)
	return message, err
}

func writeAgentMessage(writer io.Writer, message []byte) error {
	framed := binary.BigEndian.AppendUint32(nil, uint32(len(message)))
	_, err := writer.Write(append(framed, message...))
	return err
}

// agentString reads one length-prefixed string from the front of data and returns it with the rest.
func agentString(data []byte) ([]byte, []byte, bool) {
	if len(data) < 4 {
		return nil, nil, false
	}
	length := binary.BigEndian.Uint32(data)
	if uint64(len(data)-4) < uint64(length) {
		return nil, nil, false
	}
	return data[4 : 4+length], data[4+length:], true
}

// sessionBindKey returns the host key from a session-bind message, which ssh writes when it forwards an agent connection.
func sessionBindKey(message []byte) ([]byte, bool) {
	if message[0] != agentExtension {
		return nil, false
	}
	name, rest, ok := agentString(message[1:])
	if !ok || string(name) != sessionBind {
		return nil, false
	}
	key, _, ok := agentString(rest)
	return key, ok
}

// answerAgentMessages handles the agent protocol that precedes a request and returns the host keys ssh bound the
// connection to, one per hop with the Mac's own ssh first; key requests get an empty list, since no keys live here.
func answerAgentMessages(reader *bufio.Reader, writer io.Writer) ([][]byte, error) {
	var hostKeys [][]byte
	for {
		first, err := reader.Peek(1)
		if err != nil {
			return nil, err
		}
		if first[0] != 0 {
			return hostKeys, nil
		}
		message, err := readAgentMessage(reader)
		if err != nil {
			return nil, err
		}
		reply := []byte{agentFailure}
		if key, ok := sessionBindKey(message); ok {
			hostKeys, reply = append(hostKeys, key), []byte{agentSuccess}
		} else if message[0] == agentRequestIdentities {
			reply = []byte{agentIdentitiesAnswer, 0, 0, 0, 0}
		}
		if err := writeAgentMessage(writer, reply); err != nil {
			return nil, err
		}
	}
}

func respond(writer io.Writer, status int, body string) {
	fmt.Fprintf(writer, "HTTP/1.1 %d %s\r\n%s: %s\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
		status, http.StatusText(status), versionHeader, version, len(body), body)
}

func (s *server) handleConnection(conn net.Conn) {
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(10 * time.Second))
	reader := bufio.NewReader(io.LimitReader(conn, maxRequestBytes))
	hostKeys, err := answerAgentMessages(reader, conn)
	if err != nil {
		return
	}
	request, err := http.ReadRequest(reader)
	if err != nil {
		return
	}
	status, body := s.route(request, hostKeys)
	respond(conn, status, body)
}

// route answers one request. The innermost bound key names the host, since each hop binds the next; no key at all
// means a process on the Mac, which may only check that the service answers.
func (s *server) route(request *http.Request, hostKeys [][]byte) (int, string) {
	config, err := readManagedConfig(s.configPath)
	if err != nil {
		s.logger.Printf("reading config: %v", err)
		return http.StatusInternalServerError, fmt.Sprintf("the Mac cannot use its vcode-bridge config: %v\n", err)
	}
	get := request.Method == http.MethodGet
	if len(hostKeys) == 0 {
		if request.URL.Path == "/health" && get {
			return http.StatusNoContent, ""
		}
		return http.StatusForbidden, "only SSH hosts can open folders\n"
	}
	hostKey := base64.StdEncoding.EncodeToString(hostKeys[len(hostKeys)-1])

	// The installer asks which key a direct session proved before the host is installed; only the Mac's own ssh has bound it.
	if request.URL.Path == "/probe" && get {
		if len(hostKeys) != 1 {
			return http.StatusForbidden, "connect to this host straight from the Mac to install it\n"
		}
		return http.StatusOK, hostKey + "\n"
	}
	index := slices.IndexFunc(config.Hosts, func(entry hostConfig) bool { return slices.Contains(entry.HostKeys, hostKey) })
	if index < 0 {
		return http.StatusForbidden, "this host is not installed on the Mac; run install.sh with its SSH alias there\n"
	}
	// A hop whose ssh predates session-bind forwards without binding, so the innermost key would be the hop's own.
	if ownKeys := request.Header.Get(hostKeysHeader); ownKeys != "" && !slices.Contains(strings.Fields(ownKeys), hostKey) {
		return http.StatusForbidden, "a hop between this host and the Mac did not identify this host (its ssh is older than OpenSSH 8.9); ssh here from the Mac with ProxyJump instead\n"
	}

	switch {
	case request.URL.Path == "/health" && get:
		return http.StatusNoContent, ""
	case request.URL.Path == "/open" && request.Method == http.MethodPost:
		return s.open(request, config.Hosts[index].Host, config.Code)
	case request.URL.Path == "/vcode" && get:
		script, err := os.ReadFile(config.Vcode)
		if err != nil {
			return http.StatusNotFound, "the Mac has no vcode script to hand out\n"
		}
		return http.StatusOK, string(script)
	}
	return http.StatusNotFound, "not found\n"
}

func (s *server) open(request *http.Request, host, code string) (int, string) {
	body, err := io.ReadAll(io.LimitReader(request.Body, maxPathBytes+1))
	if err != nil || len(body) > maxPathBytes {
		return http.StatusBadRequest, "invalid path length\n"
	}
	remotePath, valid := validRemotePath(body)
	if !valid {
		return http.StatusBadRequest, "expected one absolute remote path\n"
	}

	// A host can open windows, so one that goes wrong is held to one a second.
	s.mutex.Lock()
	if time.Since(s.lastOpen[host]) < time.Second {
		s.mutex.Unlock()
		return http.StatusTooManyRequests, "one window a second at most\n"
	}
	s.lastOpen[host] = time.Now()
	s.mutex.Unlock()

	// VS Code guesses a file from a dot in the name, so the trailing slash vcode sends for a folder picks the option.
	option := "--file-uri"
	if strings.HasSuffix(remotePath, "/") {
		option = "--folder-uri"
	}
	target := url.URL{Scheme: "vscode-remote", Host: "ssh-remote+" + host, Path: remotePath}
	command := exec.Command(code, option, target.String())
	if err := command.Start(); err != nil {
		return http.StatusInternalServerError, "could not start VS Code\n"
	}
	go func() {
		if err := command.Wait(); err != nil {
			s.logger.Printf("%s: VS Code command exited with an error: %v", host, err)
		}
	}()
	return http.StatusAccepted, fmt.Sprintf("Opening %s on %s in VS Code\n", remotePath, host)
}

// serve accepts connections until the listener closes, a few at a time, so a misbehaving host cannot pile them up.
func (s *server) serve(listener net.Listener) {
	slots := make(chan struct{}, maxConnections)
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		slots <- struct{}{}
		go func() {
			defer func() { <-slots }()
			s.handleConnection(conn)
		}()
	}
}

// bridgeLock is held for the service's whole life. The lock file is never unlinked: replacing its inode would allow two owners.
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

// readManagedConfig reads the installed hosts; a missing file means none are installed yet.
func readManagedConfig(configPath string) (*managedConfig, error) {
	data, err := os.ReadFile(configPath)
	if errors.Is(err, fs.ErrNotExist) {
		return &managedConfig{}, nil
	}
	if err != nil {
		return nil, err
	}
	var config managedConfig
	if err := json.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", configPath, err)
	}
	if len(config.Hosts) > 0 {
		info, err := os.Stat(config.Code)
		if err != nil || info.IsDir() || info.Mode()&0111 == 0 {
			return nil, fmt.Errorf("VS Code command is not executable: %s", config.Code)
		}
	}
	hosts, keys := map[string]bool{}, map[string]string{}
	for _, entry := range config.Hosts {
		if entry.Host == "" || strings.HasPrefix(entry.Host, "-") || strings.ContainsAny(entry.Host, "/:\\ \t\r\n") || hosts[entry.Host] {
			return nil, fmt.Errorf("invalid or repeated SSH host alias %q", entry.Host)
		}
		hosts[entry.Host] = true
		if len(entry.HostKeys) == 0 {
			return nil, fmt.Errorf("no host keys for %s", entry.Host)
		}
		for _, key := range entry.HostKeys {
			if _, err := base64.StdEncoding.DecodeString(key); err != nil {
				return nil, fmt.Errorf("invalid host key for %s", entry.Host)
			}
			if other, found := keys[key]; found {
				return nil, fmt.Errorf("%s and %s share a host key, so a request could not tell them apart", other, entry.Host)
			}
			keys[key] = entry.Host
		}
	}
	return &config, nil
}

// runService serves the agent socket until ctx ends.
func runService(ctx context.Context, configPath, socketPath string) error {
	if !filepath.IsAbs(configPath) || !filepath.IsAbs(socketPath) || len(socketPath) >= 104 {
		return fmt.Errorf("-config and -socket must be absolute paths, the socket under 104 bytes")
	}
	lock, err := bridgeLock(configPath)
	if err != nil {
		return err
	}
	defer lock.Close()
	if _, err := readManagedConfig(configPath); err != nil {
		return err
	}
	listener, err := unixListener(socketPath)
	if err != nil {
		return err
	}
	defer listener.Close()

	s := &server{configPath: configPath, logger: log.Default(), lastOpen: map[string]time.Time{}}
	go s.serve(listener)
	log.Printf("vcode-bridge %s listening on %s", version, socketPath)
	<-ctx.Done()
	return nil
}

func run() error {
	configPath := flag.String("config", "", "installed host configuration file")
	socketPath := flag.String("socket", "", "agent socket that ForwardAgent points at")
	checkConfig := flag.Bool("check-config", false, "validate the installed host configuration and exit")
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
		log.SetFlags(0) // the installer shows the error as it is
		_, err := readManagedConfig(*configPath)
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return runService(ctx, *configPath, *socketPath)
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
