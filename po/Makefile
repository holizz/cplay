POFILES = $(wildcard *.po)
MOFILES = $(POFILES:.po=.mo)

$(MOFILES): $(POFILES)
	for i in $(POFILES); do msgfmt $$i -o $${i%.po}.mo; done

all: $(MOFILES)

install: all
	for i in $(MOFILES); do \
		mkdir -p $(PREFIX)/share/locale/$${i%.mo}/LC_MESSAGES; \
		install -c -m 644 $$i $(PREFIX)/share/locale/$${i%.mo}/LC_MESSAGES/cplay.mo; \
	done

clean:
	rm -f $(MOFILES) *~
