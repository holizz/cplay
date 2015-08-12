PREFIX = /usr/local
ENV = PREFIX=$(PREFIX)
.PHONY: clean lint

install: recursive-install
	install -c -m 755 cplay $(PREFIX)/bin/
	install -c -m 644 cplay.1 $(PREFIX)/man/man1

clean: recursive-clean

# pylint: R=refactor, C0103 == Invalid name
lint:
	pep8 --ignore=E1,W1 \
		cplay && \
	pylint \
		--indent-string='    ' \
		--disable=missing-docstring,bad-continuation,star-args \
		--extension-pkg-whitelist=alsaaudio \
		cplay
