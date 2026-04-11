WAILS := $(shell which wails 2>/dev/null || echo $(HOME)/go/bin/wails)

.PHONY: dev build clean

dev:
	$(WAILS) dev

build:
	$(WAILS) build -platform darwin/universal

clean:
	rm -rf build/bin frontend/dist
