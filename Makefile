# Apollo's build. `make` vets, tests and builds the binary for the machine you are on; `make help` lists the rest.
# Go fetches the pinned libraries (go/go.mod, go/go.sum) as it builds; nothing is vendored, and there is no cgo.
# GO names the go binary (default: go on the PATH; an older Go fetches the version go.mod asks for). Binaries go into
# bin/ (git ignores it).

GO     ?= go
BIN    := $(CURDIR)/bin
export GOFLAGS     := -mod=readonly -trimpath
export CGO_ENABLED := 0

# The machine: WSL and Git Bash build the Windows window (Apollo.exe); Linux and macOS build their own, for this CPU.
UNAME := $(shell uname -s)
WSL   := $(shell grep -qi microsoft /proc/version 2>/dev/null && echo yes)
ARCH  := $(shell $(GO) env GOHOSTARCH 2>/dev/null)
ifneq ($(or $(WSL),$(filter MINGW% MSYS% CYGWIN%,$(UNAME))),)
  HOST := windows
else ifeq ($(UNAME),Darwin)
  HOST := macos-$(ARCH)
else
  HOST := linux
endif

.DEFAULT_GOAL := host
.PHONY: help host all check windows linux macos macos-arm64 macos-amd64 clean

help:  ## list the targets
	@echo "This machine builds: $(HOST)"
	@grep -E '^[a-z0-9 -]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort | awk -F'\t' '{printf "  make %-12s %s\n", $$1, $$2}'

host: check $(HOST)  ## vet, test, then build for this machine (the default)

all: check windows linux macos  ## vet, test, then build every binary

check:  ## vet (Windows too) and test
	cd go && $(GO) vet ./...
	cd go && GOOS=windows GOARCH=amd64 $(GO) vet ./...
	cd go && $(GO) test -count=1 ./...

windows:  ## bin/Apollo.exe: the Windows window (icon and version info from go/rsrc_windows_amd64.syso)
	@mkdir -p $(BIN)
	cd go && GOOS=windows GOARCH=amd64 $(GO) build -ldflags "-s -w -H windowsgui" -o $(BIN)/Apollo.exe.new .
	@# a running Apollo.exe cannot be replaced: the new one waits as Apollo.exe.new until Apollo is closed and this runs again
	@mv -f $(BIN)/Apollo.exe.new $(BIN)/Apollo.exe 2>/dev/null \
		|| echo "NOTE: Apollo is open: the new build is bin/Apollo.exe.new; close Apollo and run make again"

linux:  ## bin/apollo: Linux, for this CPU (amd64 when built elsewhere)
	@mkdir -p $(BIN)
	cd go && GOOS=linux GOARCH=$(if $(filter linux,$(HOST)),$(ARCH),amd64) $(GO) build -ldflags "-s -w" -o $(BIN)/apollo .

macos: macos-arm64 macos-amd64  ## bin/apollo-darwin-*: both Macs

macos-arm64:  ## bin/apollo-darwin-arm64: Apple silicon
	@mkdir -p $(BIN)
	cd go && GOOS=darwin GOARCH=arm64 $(GO) build -ldflags "-s -w" -o $(BIN)/apollo-darwin-arm64 .

macos-amd64:  ## bin/apollo-darwin-amd64: Intel Macs
	@mkdir -p $(BIN)
	cd go && GOOS=darwin GOARCH=amd64 $(GO) build -ldflags "-s -w" -o $(BIN)/apollo-darwin-amd64 .

clean:  ## delete bin/
	rm -rf $(BIN)
