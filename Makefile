PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin

.PHONY: install uninstall test check

install:
	@mkdir -p $(BIN)
	@ln -sf $(CURDIR)/bin/lol-kde $(BIN)/lol-kde
	@echo "linked $(BIN)/lol-kde -> $(CURDIR)/bin/lol-kde"
	@command -v lol-kde >/dev/null || echo "note: $(BIN) is not on your PATH"

uninstall:
	@rm -f $(BIN)/lol-kde
	@echo "removed $(BIN)/lol-kde"

test:
	@python3 -m unittest discover -s tests -v

check:
	@python3 -m compileall -q lolkde bin && echo "syntax ok"
