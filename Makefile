PREFIX = /usr/local
ENV = PREFIX=$(PREFIX)

SUBDIRS = po

all: recursive-all

install: recursive-install
	install -c -m 755 cplay $(PREFIX)/bin
	install -c -m 644 cplay.1 $(PREFIX)/man/man1

clean: recursive-clean

recursive-all recursive-install recursive-clean:
	@target=$@; \
	for i in $(SUBDIRS); do \
		(cd $$i && make $(ENV) $${target#recursive-}); \
	done
