#!/usr/bin/env python
# -*- python -*-
"""
cplay - A curses front-end for various audio players
Copyright (C) 1998-2005 Ulf Betlehem <flu@iki.fi>

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
"""
__version__ = "cplay 1.50andreasvc"

import os
import re
import sys
import time
import glob
import string
import random
import curses
import getopt
import signal
import select
import gettext
import subprocess
if sys.version[0] < '3':
    bytes = str  # pylint: disable=redefined-builtin,invalid-name

try:
    import tty
except ImportError:
    tty = None  # pylint: disable=invalid-name

try:
    import locale
    locale.setlocale(locale.LC_ALL, '')
except (ImportError, AttributeError, locale.Error):
    pass

_locale_domain = "cplay"
_locale_dir = "/usr/share/locale"
_ = lambda x: x
gettext.install(_locale_domain, _locale_dir)

XTERM = re.search("rxvt|xterm", os.environ.get('TERM', ''))
CONTROL_FIFO = "%s/cplay-control-%s" % (
        os.environ.get("TMPDIR", "/tmp"), os.environ["USER"])
MACRO = {}
APP = None
USAGE = _("""Usage: %s [-nrRv] [ file | dir | playlist ] ...
  -n Enable restricted mode
  -r Toggles playlist repeat mode
  -R Toggles playlist random mode
  -v Toggles PCM and MASTER (default) volume control\n""")


class Application(object):
    def __init__(self):
        self.keymapstack = KeymapStack()
        self.input_mode = 0
        self.input_prompt = ""
        self.input_string = ""
        self.do_input_hook = self.stop_input_hook = None
        self.complete_input_hook = self.restore_default_status = None
        self.w = self.status = self.counter = None
        self.set_default_status = None
        self.win_status = self.win_root = self.win_tab = None
        self.win_filelist = self.win_playlist = None
        self.play_tid = self.control = None
        self.tcattr = self.timeout = self.progress = None
        self.player = self._mixer = None
        self.kludge = self.restricted = False
        self.input_keymap = Keymap()
        self.input_keymap.bind(list(string.printable), self.do_input)
        self.input_keymap.bind([127, curses.KEY_BACKSPACE],
                self.do_input, (8, ))
        self.input_keymap.bind([21, 23], self.do_input)
        self.input_keymap.bind(['\a', 27], self.cancel_input, ())
        self.input_keymap.bind(['\n', curses.KEY_ENTER], self.stop_input, ())
        for mixer in MIXERS:
            try:
                self._mixer = mixer()
            except Exception:
                pass
            else:
                break

    def command_macro(self):
        APP.do_input_hook = self.do_macro
        APP.start_input(_("macro"))

    def do_macro(self, ch):
        APP.stop_input()
        self.run_macro(chr(ch))

    def run_macro(self, c):
        for i in MACRO.get(c, ""):
            self.keymapstack.process(ord(i))

    def setup(self):
        if tty is not None:
            self.tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr[0] = tcattr[0] & ~(tty.IXON)
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSANOW, tcattr)
        self.w = curses.initscr()
        curses.cbreak()
        curses.noecho()
        try:
            curses.meta(1)
        except Exception:
            pass
        cursor(0)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, self.handler_quit)
        signal.signal(signal.SIGINT, self.handler_quit)
        signal.signal(signal.SIGTERM, self.handler_quit)
        signal.signal(signal.SIGWINCH, self.handler_resize)
        self.win_root = RootWindow(None)
        self.win_root.update()
        self.win_tab = self.win_root.win_tab
        self.win_filelist = self.win_root.win_tab.win_filelist
        self.win_playlist = self.win_root.win_tab.win_playlist
        self.win_status = self.win_root.win_status
        self.status = self.win_status.status
        self.set_default_status = self.win_status.set_default_status
        self.restore_default_status = self.win_status.restore_default_status
        self.counter = self.win_root.win_counter.counter
        self.progress = self.win_root.win_progress.progress
        self.player = PLAYERS[0]
        self.timeout = Timeout()
        self.play_tid = None
        self.kludge = False
        self.win_filelist.listdir()
        self.control = FIFOControl()

    def cleanup(self):
        try:
            curses.endwin()
        except curses.error:
            return
        if XTERM:
            sys.stderr.write("\033]0;%s\a" % "xterm")
        if tty is not None:
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, self.tcattr)
        sys.stdout.write('\n')
        try:  # remove temporary files
            os.unlink(CONTROL_FIFO)
        except (IOError, OSError):
            pass

    def run(self):
        while True:
            now = time.time()
            timeout = self.timeout.check(now)
            self.win_filelist.listdir_maybe(now)
            if not self.player.stopped:
                timeout = 0.5
                if self.kludge and self.player.poll():
                    self.player.stopped = True  # end of playlist hack
                    if not self.win_playlist.stop:
                        entry = self.win_playlist.change_active_entry(1)
                        if entry:
                            self.play(entry)
            R = [sys.stdin, self.player.stdout_r, self.player.stderr_r]
            if self.control.fd:
                R.append(self.control.fd)
            try:
                r, _w, _e = select.select(R, [], [], timeout)
            except select.error:
                continue
            self.kludge = True
            # user
            if sys.stdin in r:
                c = self.win_root.getch()
                self.keymapstack.process(c)
            # player
            if self.player.stderr_r in r:
                self.player.read_fd(self.player.stderr_r)
            # player
            if self.player.stdout_r in r:
                self.player.read_fd(self.player.stdout_r)
            # remote
            if self.control.fd in r:
                self.control.handle_command()

    def play(self, entry, offset=0):
        self.kludge = False
        self.play_tid = None
        if entry is None or offset is None:
            return
        self.player.stop(quiet=True)
        for player in PLAYERS:
            if player.re_files.search(entry.pathname):
                if player.setup(entry, offset):
                    self.player = player
                    break
        else:
            APP.status(_("Player not found!"), 1)
            # will try to play next item in playlist
            self.player.stopped = False
            return
        self.player.play()

    def delayed_play(self, entry, offset):
        if self.play_tid:
            self.timeout.remove(self.play_tid)
        self.play_tid = self.timeout.add(0.5, self.play, (entry, offset))

    def next_song(self):
        self.delayed_play(self.win_playlist.change_active_entry(1), 0)

    def prev_song(self):
        self.delayed_play(self.win_playlist.change_active_entry(-1), 0)

    def seek(self, offset, relative):
        if self.player.entry is not None:
            return
        self.player.seek(offset, relative)
        self.delayed_play(self.player.entry, self.player.offset)

    def toggle_pause(self):
        if self.player.entry is not None:
            return
        if not self.player.stopped:
            self.player.toggle_pause()

    def toggle_stop(self):
        if self.player.entry is not None:
            return
        if not self.player.stopped:
            self.player.stop()
        else:
            self.play(self.player.entry, self.player.offset)

    def key_volume(self, ch):
        self.mixer('set', (ch - ord('0')) * 10)

    def mixer(self, cmd, *args):
        if self._mixer is not None:
            getattr(self._mixer, cmd)(*args)
        APP.status(self._mixer or _("No mixer."), 1)

    def show_input(self):
        n = len(self.input_prompt) + 1
        s = cut(self.input_string, self.win_status.cols - n, left=True)
        APP.status("%s%s " % (self.input_prompt, s))

    def start_input(self, prompt="", data="", colon=True):
        self.input_mode = 1
        cursor(1)
        APP.keymapstack.push(self.input_keymap)
        self.input_prompt = prompt + (": " if colon else "")
        self.input_string = data
        self.show_input()

    def do_input(self, *args):
        if self.do_input_hook:
            return self.do_input_hook(*args)
        ch = args[0] if args else None
        if ch in [8, 127]:  # backspace
            self.input_string = self.input_string[:-1]
        elif ch == 9 and self.complete_input_hook:
            self.input_string = self.complete_input_hook(self.input_string)
        elif ch == 21:  # C-u
            self.input_string = ""
        elif ch == 23:  # C-w
            self.input_string = re.sub(r"((.* )?)\w.*", r"\1",
                    self.input_string)
        elif ch:
            self.input_string = "%s%c" % (self.input_string, ch)
        self.show_input()

    def stop_input(self, *args):
        self.input_mode = 0
        cursor(0)
        APP.keymapstack.pop()
        if not self.input_string:
            APP.status(_("cancel"), 1)
        elif self.stop_input_hook:
            self.stop_input_hook(*args)
        self.do_input_hook = self.stop_input_hook = None
        self.complete_input_hook = None

    def cancel_input(self):
        self.input_string = ""
        self.stop_input()

    def quit(self, status=0):
        self.player.stop(quiet=True)
        sys.exit(status)

    def handler_resize(self, _sig, _frame):
        # curses trickery
        while True:
            try:
                curses.endwin()
            except Exception:
                time.sleep(1)
            else:
                break
        self.w.refresh()
        self.win_root.resize()
        self.win_root.update()

    def handler_quit(self, _sig, _frame):
        self.quit(1)


class Window(object):
    def __init__(self, parent):
        self.parent = parent
        self.children = []
        self.visible = True
        self.name = self.keymap = self.w = None
        self.ypos = self.xpos = self.rows = self.cols = 0
        self.resize()
        if parent:
            parent.children.append(self)

    def insstr(self, s):
        if not s:
            return
        self.w.addstr(s[:-1])
        self.w.hline(ord(s[-1]), 1)  # insch() work-around

    def __getattr__(self, name):
        return getattr(self.w, name)

    def newwin(self):
        return curses.newwin(0, 0, 0, 0)

    def resize(self):
        self.w = self.newwin()
        self.ypos, self.xpos = self.getbegyx()
        self.rows, self.cols = self.getmaxyx()
        self.keypad(1)
        self.leaveok(0)
        self.scrollok(0)
        for child in self.children:
            child.resize()

    def update(self):
        self.clear()
        self.refresh()
        for child in self.children:
            child.update()


class ProgressWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.value = 0

    def newwin(self):
        return curses.newwin(1, self.parent.cols, self.parent.rows - 2, 0)

    def update(self):
        self.move(0, 0)
        self.hline(ord('-'), self.cols)
        if self.value > 0:
            self.move(0, 0)
            x = int(self.value * self.cols)  # 0 to cols - 1
            if x:
                self.hline(ord('='), x)
            self.move(0, x)
            self.insstr('|')
        self.touchwin()
        self.refresh()

    def progress(self, value):
        self.value = min(value, 0.99)
        self.update()


class StatusWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.default_message = ''
        self.current_message = ''
        self.tid = None

    def newwin(self):
        return curses.newwin(1, self.parent.cols - 12, self.parent.rows - 1, 0)

    def update(self):
        msg = self.current_message
        self.move(0, 0)
        self.clrtoeol()
        self.insstr(cut(msg, self.cols))
        self.touchwin()
        self.refresh()

    def status(self, message, duration=0):
        self.current_message = filter_unicode(str(message))
        if self.tid:
            APP.timeout.remove(self.tid)
        if duration:
            self.tid = APP.timeout.add(duration, self.timeout)
        else:
            self.tid = None
        self.update()

    def timeout(self):
        self.tid = None
        self.restore_default_status()

    def set_default_status(self, message):
        if self.current_message == self.default_message:
            self.status(message)
        self.default_message = message
        if XTERM:
            sys.stderr.write("\033]0;%s\a" % (message or "cplay"))

    def restore_default_status(self):
        self.status(self.default_message)


class CounterWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        # [seconds elapsed, seconds remaining of current track]
        self.values = [0, 0]
        self.mode = 1  # show remaining time

    def newwin(self):
        return curses.newwin(1, 11, self.parent.rows - 1,
                self.parent.cols - 11)

    def update(self):
        h, s = divmod(self.values[self.mode], 3600)
        m, s = divmod(s, 60)
        self.move(0, 0)
        self.attron(curses.A_BOLD)
        self.insstr("%02dh %02dm %02ds" % (h, m, s))
        self.attroff(curses.A_BOLD)
        self.touchwin()
        self.refresh()

    def counter(self, elapsed, remaining):
        if elapsed >= 0:
            self.values[0] = elapsed
        if remaining >= 0:
            self.values[1] = remaining
        self.update()

    def toggle_mode(self):
        self.mode = not self.mode
        tmp = [_("elapsed"), _("remaining")][self.mode]
        APP.status(_("Counting %s time") % tmp, 1)
        self.update()


class RootWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        keymap = Keymap()
        APP.keymapstack.push(keymap)
        self.win_progress = ProgressWindow(self)
        self.win_status = StatusWindow(self)
        self.win_counter = CounterWindow(self)
        self.win_tab = TabWindow(self)
        keymap.bind(12, self.update, ())  # C-l
        keymap.bind([curses.KEY_LEFT, 2], APP.seek, (-1, 1))  # C-b
        keymap.bind([curses.KEY_RIGHT, 6], APP.seek, (1, 1))  # C-f
        keymap.bind([1, '^'], APP.seek, (0, 0))  # C-a
        keymap.bind([5, '$'], APP.seek, (-1, 0))  # C-e
        keymap.bind(list(range(48, 58)), APP.key_volume)  # 0123456789
        keymap.bind(['+', '='], APP.mixer, ("cue", 1))
        keymap.bind('-', APP.mixer, ("cue", -1))
        keymap.bind('n', APP.next_song, ())
        keymap.bind('p', APP.prev_song, ())
        keymap.bind('z', APP.toggle_pause, ())
        keymap.bind('x', APP.toggle_stop, ())
        keymap.bind('c', self.win_counter.toggle_mode, ())
        keymap.bind('Q', APP.quit, ())
        keymap.bind('q', self.command_quit, ())
        keymap.bind('v', APP.mixer, ("toggle", ))
        keymap.bind(',', APP.command_macro, ())

    def command_quit(self):
        APP.do_input_hook = self.do_quit
        APP.start_input(_("Quit? (y/N)"))

    def do_quit(self, ch):
        if chr(ch) == 'y':
            APP.quit()
        APP.stop_input()


class TabWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.active_child = 0

        self.win_filelist = self.add(FilelistWindow)
        self.win_playlist = self.add(PlaylistWindow)
        self.win_help = self.add(HelpWindow)

        keymap = Keymap()
        keymap.bind('\t', self.change_window, ())  # tab
        keymap.bind('h', self.help, ())
        keymap.bind('D', self.win_playlist.command_delete_all, ())
        APP.keymapstack.push(keymap)
        APP.keymapstack.push(self.children[self.active_child].keymap)
        self.win_last = self.children[self.active_child]

    def newwin(self):
        return curses.newwin(self.parent.rows - 2, self.parent.cols, 0, 0)

    def update(self):
        self.update_title()
        self.move(1, 0)
        self.hline(ord('-'), self.cols)
        self.move(2, 0)
        self.clrtobot()
        self.refresh()
        child = self.children[self.active_child]
        child.visible = True
        child.update()

    def update_title(self, refresh=True):
        child = self.children[self.active_child]
        self.move(0, 0)
        self.clrtoeol()
        self.attron(curses.A_BOLD)
        self.insstr(child.get_title())
        self.attroff(curses.A_BOLD)
        if refresh:
            self.refresh()

    def add(self, cls):
        win = cls(self)
        win.visible = False
        return win

    def change_window(self, window=None):
        APP.keymapstack.pop()
        self.children[self.active_child].visible = False
        if window:
            self.active_child = self.children.index(window)
        else:
            # toggle windows 0 and 1
            self.active_child = not self.active_child
        APP.keymapstack.push(self.children[self.active_child].keymap)
        self.update()

    def help(self):
        if self.children[self.active_child] == self.win_help:
            self.change_window(self.win_last)
        else:
            self.win_last = self.children[self.active_child]
            self.change_window(self.win_help)
            APP.status(__version__, 2)


class ListWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.buffer = []
        self.bufptr = self.scrptr = self.search_direction = self.hoffset = 0
        self.last_search = ""
        self.keymap = Keymap()
        self.keymap.bind(['k', curses.KEY_UP, 16], self.cursor_move, (-1, ))
        self.keymap.bind(['j', curses.KEY_DOWN, 14], self.cursor_move, (1, ))
        self.keymap.bind('w', self.cursor_move, ("random", ))
        self.keymap.bind(['K', curses.KEY_PPAGE], self.cursor_ppage, ())
        self.keymap.bind(['J', curses.KEY_NPAGE], self.cursor_npage, ())
        self.keymap.bind(['g', curses.KEY_HOME], self.cursor_home, ())
        self.keymap.bind(['G', curses.KEY_END], self.cursor_end, ())
        self.keymap.bind(['?', 18], self.start_search,
                         (_("backward-isearch"), -1))
        self.keymap.bind(['/', 19], self.start_search,
                         (_("forward-isearch"), 1))
        self.keymap.bind(['>'], self.hscroll, (8, ))
        self.keymap.bind(['<'], self.hscroll, (-8, ))

    def newwin(self):
        return curses.newwin(self.parent.rows - 2, self.parent.cols,
                             self.parent.ypos + 2, self.parent.xpos)

    def update(self, force=True):
        self.bufptr = max(0, min(self.bufptr, len(self.buffer) - 1))
        scrptr = (self.bufptr // self.rows) * self.rows
        if force or self.scrptr != scrptr:
            self.scrptr = scrptr
            self.move(0, 0)
            self.clrtobot()
            i = 0
            for entry in self.buffer[self.scrptr:]:
                self.move(i, 0)
                i += 1
                self.putstr(entry)
                if self.getyx()[0] == self.rows - 1:
                    break
            if self.visible:
                self.refresh()
                self.parent.update_title()
        self.update_line(curses.A_REVERSE)

    def update_line(self, attr=None, refresh=True):
        if not self.buffer:
            return
        ypos = self.bufptr - self.scrptr
        if attr:
            self.attron(attr)
        self.move(ypos, 0)
        self.hline(ord(' '), self.cols)
        self.putstr(self.current())
        if attr:
            self.attroff(attr)
        if self.visible and refresh:
            self.refresh()

    def get_title(self, data=""):
        pos = "%s-%s/%s" % (self.scrptr + min(1, len(self.buffer)),
                min(self.scrptr + self.rows, len(self.buffer)),
                len(self.buffer))
        width = self.cols - len(pos) - 2
        data = cut(data, width - len(self.name), left=True)
        return "%-*s  %s" % (width, cut(self.name + data, width), pos)

    def putstr(self, entry, *pos):
        s = str(entry)
        if pos:
            self.move(*pos)
        if self.hoffset:
            s = "<%s" % s[self.hoffset + 1:]
        self.insstr(cut(s, self.cols))

    def current(self):
        if not self.buffer:
            return None
        if self.bufptr >= len(self.buffer):
            self.bufptr = len(self.buffer) - 1
        return self.buffer[self.bufptr]

    def cursor_move(self, ydiff):
        if APP.input_mode:
            APP.cancel_input()
        if not self.buffer:
            return
        self.update_line(refresh=False)
        if ydiff == "random":
            self.bufptr = random.randint(0, len(self.buffer))
        else:
            self.bufptr = (self.bufptr + ydiff) % len(self.buffer)
        self.update(force=False)

    def cursor_ppage(self):
        self.bufptr = self.scrptr - 1
        if self.bufptr < 0:
            self.bufptr = len(self.buffer) - 1
        self.scrptr = max(0, self.bufptr - self.rows)
        self.update()

    def cursor_npage(self):
        self.bufptr = self.scrptr + self.rows
        if self.bufptr > len(self.buffer) - 1:
            self.bufptr = 0
        self.scrptr = self.bufptr
        self.update()

    def cursor_home(self):
        self.cursor_move(-self.bufptr)

    def cursor_end(self):
        self.cursor_move(-self.bufptr - 1)

    def start_search(self, prompt, direction):
        self.search_direction = direction
        if APP.input_mode:
            APP.input_prompt = "%s: " % prompt
            self.do_search(advance=direction)
        else:
            APP.do_input_hook = self.do_search
            APP.stop_input_hook = self.stop_search
            APP.start_input(prompt)

    def stop_search(self):
        self.last_search = APP.input_string
        APP.status(_("ok"), 1)

    def do_search(self, ch=None, advance=0):
        if ch in [8, 127]:
            APP.input_string = APP.input_string[:-1]
        elif ch:
            APP.input_string = "%s%c" % (APP.input_string, ch)
        else:
            APP.input_string = APP.input_string or self.last_search
        origin = index = (self.bufptr + advance) % len(self.buffer)
        while True:
            line = str(self.buffer[index]).lower()
            if line.find(APP.input_string.lower()) != -1:
                APP.show_input()
                self.update_line(refresh=False)
                self.bufptr = index
                self.update(force=False)
                break
            index = (index + self.search_direction) % len(self.buffer)
            if index == origin:
                APP.status(_("Not found: %s ") % APP.input_string)
                break

    def hscroll(self, value):
        self.hoffset = max(0, self.hoffset + value)
        self.update()


class HelpWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.name = _("Help")
        self.keymap.bind('q', self.parent.help, ())
        self.buffer = _("""\
  Global                               t, T  : tag current/regex
  ------                               u, U  : untag current/regex
  Up, Down, k, j, C-p, C-n,            Sp, i : invert current/all
  PgUp, PgDn, K, J,                    !, ,  : shell, macro
  Home, End, g, G : movement
  Enter           : chdir or play      Filelist
  Tab             : filelist/playlist  --------
  n, p            : next/prev track    a     : add (tagged) to playlist
  z, x            : toggle pause/stop  s, S  : recursive search, toggle sort
  Left, Right,                         BS, o : goto parent/specified dir
  C-f, C-b    : seek forward/backward  m, '  : set/get bookmark
  C-a, C-e    : restart/end track
  C-s, C-r, / : isearch                Playlist
  C-g, Esc    : cancel                 --------
  1..9, +, -  : volume control         d     : delete (tagged) tracks
  c, v        : counter/volume mode    m, M  : move tagged tracks after/before
  <, >        : horizontal scrolling   r, R  : toggle repeat/Random mode
  C-l, l      : refresh, list mode     s, S  : shuffle/Sort playlist
  D           : clear playlist         w, @  : write playlist, jump to active
  h, q, Q     : help, quit?, Quit!     X     : stop playlist after each track
""").splitlines()


class ListEntry(object):
    def __init__(self, pathname, directory=False):
        self.filename = os.path.basename(pathname)
        self.pathname = pathname
        self.slash = '/' if directory else ''
        self.tagged = False

    def __str__(self):
        mark = '#' if self.tagged else ' '
        return "%s %s%s" % (mark, filter_unicode(self.vp()), self.slash)

    def vp(self):
        return self.vps[0][1](self)

    def vp_filename(self):
        return self.filename or self.pathname

    def vp_pathname(self):
        return self.pathname

    vps = [[_("filename"), vp_filename],
           [_("pathname"), vp_pathname]]


class PlaylistEntry(ListEntry):
    def __init__(self, pathname):
        ListEntry.__init__(self, pathname)
        self.metadata = None
        self.active = False

    def vp_metadata(self):
        return self.metadata or self.read_metadata()

    def read_metadata(self):
        self.metadata = get_tag(self.pathname)
        return self.metadata

    vps = ListEntry.vps[:] + [[_("metadata"), vp_metadata]]


class TagListWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.tag_value = None
        self.keymap.bind(' ', self.command_tag_untag, ())
        self.keymap.bind('i', self.command_invert_tags, ())
        self.keymap.bind('t', self.command_tag, (True, ))
        self.keymap.bind('u', self.command_tag, (False, ))
        self.keymap.bind('T', self.command_tag_regexp, (True, ))
        self.keymap.bind('U', self.command_tag_regexp, (False, ))
        self.keymap.bind('l', self.command_change_viewpoint, ())
        self.keymap.bind('!', self.command_shell, ())

    def command_shell(self):
        if APP.restricted:
            return
        APP.stop_input_hook = self.stop_shell
        APP.complete_input_hook = self.complete_shell
        APP.start_input(_("shell$ "), colon=False)

    def stop_shell(self):
        curses.endwin()
        sys.stderr.write("\n")
        argv = [x.pathname for x in self.get_tagged()]
        if not argv and self.current():
            argv.append(self.current().pathname)
        argv = [APP.input_string, "--"] + argv
        result = subprocess.call(argv, shell=True)
        if result == 0:
            sys.stderr.write("\npress return to continue..\n")
        else:
            sys.stderr.write("\nshell returned %s, press return!\n" % result)
        sys.stdin.readline()
        APP.win_root.update()
        APP.restore_default_status()
        cursor(0)

    def complete_shell(self, line):
        return self.complete_generic(line, quote=True)

    def complete_generic(self, line, quote=False):
        if quote:
            s = re.sub(r'.*[^\\][ \'"()\[\]{}$`]', '', line)
            s, part = re.sub(r'\\', '', s), line[:len(line) - len(s)]
        else:
            s, part = line, ""
        results = glob.glob(os.path.expanduser(s) + "*")
        if not results:
            return line
        elif len(results) == 1:
            lm = results[0]
            if os.path.isdir(lm):
                lm += '/'
        else:
            lm = results[0]
            for result in results:
                for i in range(min(len(result), len(lm))):
                    if result[i] != lm[i]:
                        lm = lm[:i]
                        break
        if quote:
            lm = re.sub(r'([ \'"()\[\]{}$`])', r'\\\1', lm)
        return part + lm

    def command_change_viewpoint(self, cls=ListEntry):
        cls.vps.append(cls.vps.pop(0))
        APP.status(_("Listing %s") % cls.vps[0][0], 1)
        APP.player.update_status()
        self.update()

    def command_invert_tags(self):
        for i in self.buffer:
            i.tagged = not i.tagged
        self.update()

    def command_tag_untag(self):
        if not self.buffer:
            return
        tmp = self.buffer[self.bufptr]
        tmp.tagged = not tmp.tagged
        self.cursor_move(1)

    def command_tag(self, value):
        if not self.buffer:
            return
        self.buffer[self.bufptr].tagged = value
        self.cursor_move(1)

    def command_tag_regexp(self, value):
        self.tag_value = value
        APP.stop_input_hook = self.stop_tag_regexp
        APP.start_input(_("tag regexp") if value else _("untag regexp"))

    def stop_tag_regexp(self):
        try:
            r = re.compile(APP.input_string, re.I)
            for entry in self.buffer:
                if r.search(str(entry)):
                    entry.tagged = self.tag_value
            self.update()
            APP.status(_("ok"), 1)
        except re.error as err:
            APP.status(err, 2)

    def get_tagged(self):
        return [x for x in self.buffer if x.tagged]

    def not_tagged(self, l):
        return [x for x in l if not x.tagged]


class FilelistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.oldposition = {}
        self.cwd = None
        try:
            self.chdir(os.getcwd())
        except OSError:
            self.chdir(os.environ['HOME'])
        self.startdir = self.cwd
        self.mtime_when = 0
        self.mtime = None
        self.sortdate = self.search_mode = False
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_chdir_or_play, ())
        self.keymap.bind(['.', 127, curses.KEY_BACKSPACE],
                         self.command_chparentdir, ())
        self.keymap.bind('a', self.command_add_recursively, ())
        self.keymap.bind('o', self.command_goto, ())
        self.keymap.bind('s', self.command_search_recursively, ())
        self.keymap.bind('S', self.togglesort, ())
        self.keymap.bind('m', self.command_set_bookmark, ())
        self.keymap.bind("'", self.command_get_bookmark, ())
        self.bookmarks = {39: [self.cwd, 0]}

    def togglesort(self):
        self.sortdate = not self.sortdate
        self.listdir(prevdir=self.buffer[self.bufptr].filename)
        APP.status(_("Sort filelist by %s" % (
                'name', 'modification time')[self.sortdate]), 1)

    def command_get_bookmark(self):
        APP.do_input_hook = self.do_get_bookmark
        APP.start_input(_("bookmark"))

    def do_get_bookmark(self, ch):
        APP.input_string = ch
        bookmark = self.bookmarks.get(ch)
        if bookmark:
            self.bookmarks[39] = [self.cwd, self.bufptr]
            directory, pos = bookmark
            self.chdir(directory)
            self.listdir()
            self.bufptr = pos
            self.update()
            APP.status(_("ok"), 1)
        else:
            APP.status(_("Not found!"), 1)
        APP.stop_input()

    def command_set_bookmark(self):
        APP.do_input_hook = self.do_set_bookmark
        APP.start_input(_("set bookmark"))

    def do_set_bookmark(self, ch):
        APP.input_string = ch
        self.bookmarks[ch] = [self.cwd, self.bufptr]
        if ch:
            APP.status(_("ok"), 1)
        else:
            APP.stop_input()

    def command_search_recursively(self):
        APP.stop_input_hook = self.stop_search_recursively
        APP.start_input(_("search"))

    def stop_search_recursively(self):
        try:
            re_tmp = re.compile(APP.input_string, re.I)
        except re.error as err:
            APP.status(err, 2)
            return
        APP.status(_("Searching..."))
        results = []
        for entry in self.buffer:
            if entry.filename == "..":
                continue
            if re_tmp.search(entry.filename):
                results.append(entry)
            elif os.path.isdir(entry.pathname):
                try:
                    self.search_recursively(re_tmp, entry.pathname, results)
                except Exception:
                    pass
        if not self.search_mode:
            self.chdir(os.path.join(self.cwd, _("search results")))
            self.search_mode = True
        self.buffer = results
        self.bufptr = 0
        self.parent.update_title()
        self.update()
        APP.restore_default_status()

    def search_recursively(self, re_tmp, directory, results):
        for filename in os.listdir(directory):
            pathname = os.path.join(directory, filename)
            if re_tmp.search(filename):
                if os.path.isdir(pathname):
                    results.append(ListEntry(pathname, True))
                elif valid_playlist(filename) or valid_song(filename):
                    results.append(ListEntry(pathname))
            elif os.path.isdir(pathname):
                self.search_recursively(re_tmp, pathname, results)

    def get_title(self):
        self.name = _("Filelist: ")
        return ListWindow.get_title(self, re.sub("/?$", "/", self.cwd))

    def listdir_maybe(self, now=0):
        if now < self.mtime_when + 2:
            return
        self.mtime_when = now
        self.oldposition[self.cwd] = self.bufptr
        try:
            if self.mtime != int(os.stat(self.cwd).st_mtime):
                self.listdir(quiet=True)
        except OSError:
            pass

    def listdir(self, quiet=False, prevdir=None):
        if not quiet:
            APP.status(_("Reading directory..."))
        self.search_mode = False
        dirs = []
        files = []
        try:
            self.mtime = int(os.stat(self.cwd).st_mtime)
            self.mtime_when = time.time()
            filenames = os.listdir(self.cwd)
            if self.sortdate:
                filenames.sort(key=lambda x: os.stat(x).st_mtime)
            else:
                filenames.sort()
            for filename in filenames:
                if filename[0] == ".":
                    continue
                pathname = os.path.join(self.cwd, filename)
                if os.path.isdir(pathname):
                    dirs.append(pathname)
                elif valid_song(filename):
                    files.append(pathname)
                elif valid_playlist(filename):
                    files.append(pathname)
        except OSError:
            pass
        self.buffer = []
        for i in dirs:
            self.buffer.append(ListEntry(i, True))
        for i in files:
            self.buffer.append(ListEntry(i))
        if prevdir:
            for bufptr in range(len(self.buffer)):
                if self.buffer[bufptr].filename == prevdir:
                    self.bufptr = bufptr
                    break
            else:
                self.bufptr = 0
        elif self.cwd in self.oldposition:
            self.bufptr = self.oldposition[self.cwd]
        else:
            self.bufptr = 0
        self.parent.update_title()
        self.update()
        if not quiet:
            APP.restore_default_status()

    def chdir(self, directory):
        if self.cwd is not None:
            self.oldposition[self.cwd] = self.bufptr
        self.cwd = os.path.normpath(directory)
        try:
            os.chdir(self.cwd)
        except OSError:
            pass

    def command_chdir_or_play(self):
        if not self.buffer:
            return
        if self.current().filename == "..":
            self.command_chparentdir()
        elif os.path.isdir(self.current().pathname):
            self.chdir(self.current().pathname)
            self.listdir()
        elif valid_song(self.current().filename):
            APP.play(self.current())

    def command_chparentdir(self):
        if APP.restricted and self.cwd == self.startdir:
            return
        directory = os.path.basename(self.cwd)
        self.chdir(os.path.dirname(self.cwd))
        self.listdir(prevdir=directory)

    def command_goto(self):
        if APP.restricted:
            return
        APP.stop_input_hook = self.stop_goto
        APP.complete_input_hook = self.complete_generic
        APP.start_input(_("goto"))

    def stop_goto(self):
        directory = os.path.expanduser(APP.input_string)
        if directory[0] != '/':
            directory = os.path.join(self.cwd, directory)
        if not os.path.isdir(directory):
            APP.status(_("Not a directory!"), 1)
            return
        self.chdir(directory)
        self.listdir()

    def command_add_recursively(self):
        l = self.get_tagged()
        if not l:
            APP.win_playlist.add(self.current().pathname)
            self.cursor_move(1)
            return
        APP.status(_("Adding tagged files"), 1)
        for entry in l:
            APP.win_playlist.add(entry.pathname, quiet=True)
            entry.tagged = False
        self.update()


class PlaylistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.pathname = self.active_entry = None
        self.repeat = self.random = self.stop = False
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_play, ())
        self.keymap.bind('d', self.command_delete, ())
        self.keymap.bind('m', self.command_move, (True, ))
        self.keymap.bind('M', self.command_move, (False, ))
        self.keymap.bind('s', self.command_shuffle, ())
        self.keymap.bind('S', self.command_sort, ())
        self.keymap.bind('r', self.command_toggle_repeat, ())
        self.keymap.bind('R', self.command_toggle_random, ())
        self.keymap.bind('X', self.command_toggle_stop, ())
        self.keymap.bind('w', self.command_save_playlist, ())
        self.keymap.bind('@', self.command_jump_to_active, ())
        self.keymap.bind(['.', 127, curses.KEY_BACKSPACE],
                lambda: (APP.win_tab.change_window()
                    or APP.win_filelist.command_chparentdir()), ())

    def command_change_viewpoint(self, cls=PlaylistEntry):
        TagListWindow.command_change_viewpoint(self, cls)

    def get_title(self):
        space_out = lambda value, s: s if value else (' ' * len(s))
        self.name = _("Playlist %s %s %s") % (
            space_out(self.repeat, _("[repeat]")),
            space_out(self.random, _("[random]")),
            space_out(self.stop, _("[stop]")))
        return ListWindow.get_title(self)

    def append(self, item):
        self.buffer.append(item)
        if self.random:
            self.random_left.append(item)

    def add_dir(self, directory):
        try:
            filenames = sorted(os.listdir(directory))
            subdirs = []
            for filename in filenames:
                pathname = os.path.join(directory, filename)
                if valid_song(filename):
                    self.append(PlaylistEntry(pathname))
                if os.path.isdir(pathname):
                    subdirs.append(pathname)
            for subdir in subdirs:
                self.add_dir(subdir)
        except Exception as err:
            APP.status(err, 2)

    def add_m3u(self, line):
        if re.match(r"^(#.*)?$", line):
            return
        if re.match(r"^(/|http://)", line):
            self.append(PlaylistEntry(line))
        else:
            dirname = os.path.dirname(self.pathname)
            self.append(PlaylistEntry(os.path.join(dirname, line)))

    def add_pls(self, line):
        # todo - support title & length
        m = re.match(r"File(\d + )=(.*)", line)
        if m:
            self.append(PlaylistEntry(m.group(2)))

    def add_playlist(self, pathname):
        self.pathname = pathname
        if re.search(r"\.m3u$", pathname, re.I):
            f = self.add_m3u
        elif re.search(r"\.pls$", pathname, re.I):
            f = self.add_pls
        for line in open(pathname):
            f(line.strip())

    def add(self, pathname, quiet=False):
        try:
            if os.path.isdir(pathname):
                APP.status(_("Working..."))
                self.add_dir(os.path.abspath(pathname))
            elif valid_playlist(pathname):
                self.add_playlist(pathname)
            elif valid_song(pathname):
                self.append(PlaylistEntry(pathname))
            else:
                return
            # todo - refactor
            filename = os.path.basename(pathname) or pathname
            if not quiet:
                self.update()
                APP.status(_("Added: %s") % filename, 1)
        except Exception as err:
            APP.status(err, 2)

    def putstr(self, entry, *pos):
        if entry.active:
            self.attron(curses.A_BOLD)
        ListWindow.putstr(self, entry, *pos)
        if entry.active:
            self.attroff(curses.A_BOLD)

    def change_active_entry(self, direction):
        if not self.buffer:
            return
        old = self.active_entry
        new = None
        if self.random:
            if direction > 0:
                if self.random_next:
                    new = self.random_next.pop()
                elif self.random_left:
                    pass
                elif self.repeat:
                    self.random_left = self.buffer[:]
                else:
                    return
                if new is None:
                    new = random.choice(self.random_left)
                    self.random_left.remove(new)
                try:
                    self.random_prev.remove(new)
                except ValueError:
                    pass
                self.random_prev.append(new)
            else:
                if len(self.random_prev) > 1:
                    self.random_next.append(self.random_prev.pop())
                    new = self.random_prev[-1]
                else:
                    return
            if old:
                old.active = False
        elif old:
            index = self.buffer.index(old) + direction
            if not (0 <= index < len(self.buffer) or self.repeat):
                return
            old.active = False
            new = self.buffer[index % len(self.buffer)]
        else:
            new = self.buffer[0]
        new.active = True
        self.active_entry = new
        self.update()
        return new

    def command_jump_to_active(self):
        entry = self.active_entry
        if entry is None:
            return
        self.bufptr = self.buffer.index(entry)
        self.update()

    def command_play(self):
        if not self.buffer:
            return
        entry = self.active_entry
        if entry is not None:
            entry.active = False
        entry = self.current()
        entry.active = True
        self.active_entry = entry
        self.update()
        APP.play(entry)

    def command_delete(self):
        if not self.buffer:
            return
        current_entry, n = self.current(), len(self.buffer)
        self.buffer = self.not_tagged(self.buffer)
        if n > len(self.buffer):
            try:
                self.bufptr = self.buffer.index(current_entry)
            except ValueError:
                pass
        else:
            current_entry.tagged = False
            del self.buffer[self.bufptr]
        if self.active_entry not in self.buffer:
            self.active_entry = None
        if self.random:
            self.random_prev = self.not_tagged(self.random_prev)
            self.random_next = self.not_tagged(self.random_next)
            self.random_left = self.not_tagged(self.random_left)
        self.update()

    def command_delete_all(self):
        self.buffer = []
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.active_entry = None
        APP.status(_("Playlist cleared"), 1)
        self.update()

    def command_move(self, after):
        if not self.buffer:
            return
        current_entry, l = self.current(), self.get_tagged()
        if not l or current_entry.tagged:
            return
        self.buffer = self.not_tagged(self.buffer)
        self.bufptr = self.buffer.index(current_entry) + after
        self.buffer[self.bufptr:self.bufptr] = l
        self.update()

    def command_shuffle(self):
        l = []
        n = len(self.buffer)
        while n > 0:
            n -= 1
            r = random.randint(0, n)
            l.append(self.buffer[r])
            del self.buffer[r]
        self.buffer = l
        self.bufptr = 0
        self.update()
        APP.status(_("Shuffled playlist... Oops?"), 1)

    def command_sort(self):
        APP.status(_("Working..."))
        self.buffer.sort(key=lambda x: x.vp() or -1)
        self.bufptr = 0
        self.update()
        APP.status(_("Sorted playlist"), 1)

    def command_toggle_repeat(self):
        self.toggle("repeat", _("Repeat: %s"))

    def command_toggle_random(self):
        self.toggle("random", _("Random: %s"))
        self.random_prev = []
        self.random_next = []
        self.random_left = self.buffer[:]

    def command_toggle_stop(self):
        self.toggle("stop", _("Stop playlist: %s"))

    def toggle(self, attr, msg):
        setattr(self, attr, not getattr(self, attr))
        APP.status(msg % (_('on') if getattr(self, attr) else _("off")), 1)
        self.parent.update_title()

    def command_save_playlist(self):
        if APP.restricted:
            return
        default = self.pathname or "%s/" % APP.win_filelist.cwd
        APP.stop_input_hook = self.stop_save_playlist
        APP.start_input(_("save playlist"), default)

    def stop_save_playlist(self):
        pathname = APP.input_string
        if not pathname or pathname == APP.win_filelist.cwd + '/':
            APP.status(_("Cancelled"), 2)
            return
        if pathname[0] != '/':
            pathname = os.path.join(APP.win_filelist.cwd, pathname)
        if not re.search(r"\.m3u$", pathname, re.I):
            pathname = "%s%s" % (pathname, ".m3u")
        try:
            with open(pathname, "w") as out:
                out.writelines("%s\n" % item.pathname for item in self.buffer)
            self.pathname = pathname
            APP.status(_("ok"), 1)
        except IOError as err:
            APP.status(err, 2)


class Player(object):
    def __init__(self, commandline, files, fps=1):
        self.commandline = commandline
        self.re_files = re.compile(files, re.I)
        self.fps = fps
        self.stdin_r, self.stdin_w = os.pipe()
        self.stdout_r, self.stdout_w = os.pipe()
        self.stderr_r, self.stderr_w = os.pipe()
        self.stopped = self.paused = False
        self.entry = self.time_setup = None
        self.offset = self.step = self.length = 0
        self.buf = ''
        self.tid = self.argv = self._proc = None

    def setup(self, entry, offset):
        self.argv = self.commandline.split()
        self.argv[0] = which(self.argv[0])
        for i in range(len(self.argv)):
            if self.argv[i] == "%s":
                self.argv[i] = entry.pathname
            elif self.argv[i] == "%d":
                self.argv[i] = str(offset * self.fps)
        self.entry = entry
        self.offset = offset
        if offset == 0:
            APP.progress(0)
            self.offset = self.length = 0
        self.time_setup = time.time()
        return self.argv[0]

    def play(self):
        try:
            self._proc = subprocess.Popen(
                    self.argv,
                    stdout=self.stdout_w,
                    stderr=self.stderr_w,
                    stdin=self.stdin_r,
                    shell=False)
        except OSError as err:
            APP.status(err, 2)
            return False
        self.stopped = self.paused = False
        self.step = 0
        self.update_status()
        return True

    def stop(self, quiet=False):
        if self._proc is None:
            return
        if self.paused:
            self.toggle_pause(quiet)
        if self.poll() is None:
            try:
                self._proc.terminate()
            except OSError as err:
                APP.status(err, 2)
        self.stopped = True
        if not quiet:
            self.update_status()

    def toggle_pause(self, quiet=False):
        if self._proc is None:
            return
        self._proc.send_signal(signal.SIGCONT if self.paused
                else signal.SIGSTOP)
        self.paused = not self.paused
        if not quiet:
            self.update_status()

    def parse_progress(self):
        if self.stopped or self.step:
            self.tid = None
        else:
            self.parse_buf()
            self.tid = APP.timeout.add(1.0, self.parse_progress)

    def parse_buf(self):
        raise NotImplementedError

    def read_fd(self, fd):
        self.buf = os.read(fd, 512)
        if self.tid is None:
            self.parse_progress()

    def poll(self):
        if self.stopped or self._proc is None:
            return 0
        elif self._proc.poll() is not None:
            self._proc = None
            APP.set_default_status("")
            APP.counter(0, 0)
            APP.progress(0)
            return True

    def seek(self, offset, relative):
        if relative:
            d = offset * self.length * 0.002
            self.step = self.step * (self.step * d > 0) + d
            self.offset = min(self.length, max(0, self.offset + self.step))
        else:
            self.step = 1
            self.offset = self.length + offset if (offset < 0) else offset
        self.show_position()

    def set_position(self, offset, length):
        self.offset = offset
        self.length = length
        self.show_position()

    def show_position(self):
        APP.counter(self.offset, self.length - self.offset)
        APP.progress(self.length and (float(self.offset) / self.length))

    def update_status(self):
        if self.entry is None:
            APP.set_default_status("")
        elif self.stopped:
            APP.set_default_status(_("Stopped: %s") % self.entry.vp())
        elif self.paused:
            APP.set_default_status(_("Paused: %s") % self.entry.vp())
        else:
            APP.set_default_status(_("Playing: %s") % self.entry.vp())


class FrameOffsetPlayer(Player):
    re_progress = re.compile(
            r"Time.*\s(\d+):(\d+).*\[(\d+):(\d+)".encode('ascii'))

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = [int(x) for x in match.groups()]
            head, tail = m1 * 60 + s1, m2 * 60 + s2
            self.set_position(head, head + tail)


class TimeOffsetPlayer(Player):
    re_progress = re.compile(r"(\d+):(\d+):(\d+)".encode('ascii'))

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            h, m, s = [int(x) for x in match.groups()]
            tail = h * 3600 + m * 60 + s
            head = max(self.length, tail) - tail
            self.set_position(head, head + tail)


class MPlayer(Player):
    # e.g.: "A:   3.2 (03.1) of 306.8 (05:06.7)  0.4%"
    re_progress = re.compile(br"^A:.*?(\d+)\.\d \([^)]+\) of (\d+)\.\d")

    def play(self):
        Player.play(self)
        self.mplayer_send("seek %d 2\n" % self.offset)

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            position, length = [int(x) for x in match.groups()]
            self.set_position(position, length)

    def mplayer_send(self, arg):
        try:
            os.write(self.stdin_w, arg + "\n")
        except IOError:
            APP.status("ERROR: Cannot send commands to mplayer!", 3)


class GSTPlayer(Player):
    re_progress = re.compile(br"Time: (\d+):(\d+):(\d+).\d+"
            br" of (\d+):(\d+):(\d+).\d+")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            ph, pm, ps, lh, lm, ls = [int(x) for x in match.groups()]
            position = ph * 3600 + pm * 60 + ps
            length = lh * 3600 + lm * 60 + ls
            self.set_position(position, length)


class NoOffsetPlayer(Player):
    def parse_buf(self):
        head = self.offset + 1
        self.set_position(head, 0)

    def seek(self, *unused_args):
        return 1


class Timeout(object):
    def __init__(self):
        self._next = 0
        self._dict = {}

    def add(self, timeout, func, args=()):
        self._next += 1
        tid = self._next
        self._dict[tid] = (func, args, time.time() + timeout)
        return tid

    def remove(self, tid):
        del self._dict[tid]

    def check(self, now):
        for tid, (func, args, timeout) in list(self._dict.items()):
            if now >= timeout:
                self.remove(tid)
                func(*args)
        return 0.2 if self._dict else None


class FIFOControl(object):
    def __init__(self):
        self.commands = {
            "pause": [APP.toggle_pause, []],
            "next": [APP.next_song, []],
            "prev": [APP.prev_song, []],
            "forward": [APP.seek, [1, 1]],
            "backward": [APP.seek, [-1, 1]],
            "play": [APP.toggle_stop, []],
            "stop": [APP.toggle_stop, []],
            "volume": [self.volume, None],
            "macro": [APP.run_macro, None],
            "add": [APP.win_playlist.add, None],
            "empty": [APP.win_playlist.command_delete_all, []],
            "quit": [APP.quit, []]}
        self.fd = None
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
            os.mkfifo(CONTROL_FIFO, 0o600)
            self.fd = open(CONTROL_FIFO, "r+b", 0)
        except IOError:
            # warn that we're disabling the fifo because someone raced us?
            return

    def handle_command(self):
        argv = self.fd.readline().strip().split(None, 1)
        if argv[0] in self.commands:
            f, a = self.commands[argv[0]]
            if a is None:
                a = argv[1:]
            f(*a)

    def volume(self, s):
        argv = s.split()
        APP.mixer(argv[0], int(argv[1]))


class Mixer(object):
    def __init__(self):
        self._channels = []

    def get(self):
        raise NotImplementedError

    def set(self, level):
        raise NotImplementedError

    def cue(self, increment):
        if increment == 0:
            return
        newvolume = oldvolume = self.get()
        while self.get() == oldvolume:
            newvolume = min(max(0, newvolume + increment), 100)
            self.set(newvolume)
            if ((oldvolume == 0 and increment < 0)
                    or (oldvolume == 100 and increment > 0)):
                break

    def toggle(self):
        self._channels.append(self._channels.pop(0))

    def __str__(self):
        return _("%s volume %s%%") % (self._channels[0], self.get())


class OssMixer(Mixer):
    def __init__(self):
        import ossaudiodev
        self._mixer = ossaudiodev.openmixer()
        self._get, self._set = self._mixer.get, self._mixer.set
        self._channels = [['PCM', ossaudiodev.SOUND_MIXER_PCM],
                    ['Master', ossaudiodev.SOUND_MIXER_VOLUME]]
        self.toggle()

    def get(self):
        return self._get(self._channels[0][1])[0]

    def set(self, level):
        self._set(self._channels[0][1], (level, level))

    def __str__(self):
        return _("%s volume %s%%") % (self._channels[0][0], self.get())

    def close(self):
        self._mixer.close()


class AlsaMixer(Mixer):
    def __init__(self):
        import alsaaudio
        self._Mixer = alsaaudio.Mixer
        self._channels = ['PCM', 'Master']
        self._mixer = self._Mixer(self._channels[0])

    def toggle(self):
        self._channels.append(self._channels.pop(0))
        self._mixer.close()
        self._mixer = self._Mixer(self._channels[0])

    def get(self):
        return self._mixer.getvolume()[0]

    def set(self, level):
        self._mixer.setvolume(level)

    def close(self):
        self._mixer.close()


class PulseMixer(Mixer):
    def __init__(self):
        self._channels = ['Master']
        self._sink = re.search(r'Sink #([0-9]+)', self._list_sinks()).group(1)
        self.set(self.get())

    def _list_sinks(self):
        return subprocess.Popen(['pactl', 'list', 'sinks'],
                shell=False, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE).communicate()[0]

    def get(self):
        return int(re.search(r'Volume: .* ([0-9]+)%',
                self._list_sinks()).group(1))

    def set(self, vol):
        subprocess.check_call([
                'pactl', '--', 'set-sink-volume', self._sink, '%s%%' % vol])

    def cue(self, inc):
        self.set('%+d' % inc)


class Stack(object):
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        return self.items.pop()


class KeymapStack(Stack):
    def process(self, code):
        for keymap in reversed(self.items):
            if keymap and keymap.process(code):
                break


class Keymap(object):
    def __init__(self):
        self.methods = [None] * curses.KEY_MAX

    def bind(self, key, method, args=None):
        if isinstance(key, (tuple, list)):
            for i in key:
                self.bind(i, method, args)
            return
        if isinstance(key, (str, bytes)):
            key = ord(key)
        self.methods[key] = (method, args)

    def process(self, key):
        if self.methods[key] is None:
            return False
        method, args = self.methods[key]
        if args is None:
            args = (key, )
        method(*args)
        return True


def get_tag(pathname):
    if re.compile(r"^http://").match(pathname) or not os.path.exists(pathname):
        return pathname
    result = None
    try:
        import mutagen
        metadata = mutagen.File(pathname, easy=True)
    except Exception:
        return os.path.basename(pathname)
    if metadata:
        tags = [metadata.get('tracknumber'),
                metadata.get('artist'),
                metadata.get('title', metadata.get('album'))]
        if tags[0] and len(tags[0][0]) == 1:  # pad single digit track number
            tags[0][0] = tags[0][0].rjust(2, '0')
        result = ' - '.join(tag[0] for tag in tags if tag and tag[0])
        result = result.encode(sys.getfilesystemencoding())
    return result or os.path.basename(pathname)


def valid_song(name):
    return any(player.re_files.search(name) for player in PLAYERS)


def valid_playlist(name):
    return re.search(r"\.(m3u|pls)$", name, re.I)


def which(program):
    for path in os.environ.get('PATH', os.defpath).split(":"):
        if path and os.path.exists(os.path.join(path, program)):
            return os.path.join(path, program)


def cut(s, n, left=False):
    if len(s) <= n:
        return s
    elif left:
        return "<%s" % s[-n + 1:]
    else:
        return "%s>" % s[:n - 1]


def filter_unicode(s):
    if isinstance(s, bytes):
        return s.decode(sys.getfilesystemencoding(), 'replace').encode('utf8')
    else:
        return s.encode(sys.getfilesystemencoding(), 'replace').decode(
                sys.getfilesystemencoding())


def cursor(visibility):
    try:
        curses.curs_set(visibility)
    except Exception:
        pass


def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "nrRv")
    except getopt.GetoptError:
        sys.stderr.write(USAGE % os.path.basename(sys.argv[0]))
        sys.exit(1)

    global APP
    APP = Application()

    playlist = []
    if not sys.stdin.isatty():
        playlist = [x.strip() for x in sys.stdin]
        os.close(0)
        os.open("/dev/tty", 0)
    try:
        APP.setup()
        for opt in dict(opts):
            if opt == "-n":
                APP.restricted = True
            elif opt == "-r":
                APP.win_playlist.command_toggle_repeat()
            elif opt == "-R":
                APP.win_playlist.command_toggle_random()
            elif opt == "-v":
                APP.mixer("toggle")
        if args or playlist:
            for i in args or playlist:
                if os.path.exists(i):
                    i = os.path.abspath(i)
                APP.win_playlist.add(i)
            APP.win_tab.change_window()
        APP.run()
    except SystemExit:
        APP.cleanup()
    except Exception:
        APP.cleanup()
        import traceback
        traceback.print_exc()


MIXERS = [OssMixer, AlsaMixer, PulseMixer]
PLAYERS = [
    FrameOffsetPlayer("ogg123 -q -v -k %d %s",
            r"\.ogg$"),
    FrameOffsetPlayer("splay -f -k %d %s",
            r"(^http://|\.mp[123]$)", 38.28),
    FrameOffsetPlayer("mpg123 -q -v -k %d %s",
            r"(^http://|\.mp[123]$)", 38.28),
    FrameOffsetPlayer("mpg321 -q -v -k %d %s",
            r"(^http://|\.mp[123]$)", 38.28),
    TimeOffsetPlayer("madplay -v --display-time=remaining -s %d %s",
            r"\.mp[123]$"),
    MPlayer("mplayer -slave -vc null -vo null %s",
            r"^http://|\.(mp[123cp+]|ogg|flac|spx|cdr|wav|aiff|ape|m4a|wma"
            r"|mod|xm|fm|s3m|med|col|669|it|mtm|stm|au)$"),
    GSTPlayer("gst123 -x -k %d %s",
            r"\.(mp[123]|ogg|opus|oga|flac|wav|m4a|m4b|aiff)$"),
    NoOffsetPlayer("mikmod -q -p0 %s",
            r"\.(mod|xm|fm|s3m|med|col|669|it|mtm)$"),
    NoOffsetPlayer("xmp -q %s",
            r"\.(mod|xm|fm|s3m|med|col|669|it|mtm|stm)$"),
    NoOffsetPlayer("play %s",
            r"\.(aiff|au|cdr|mp3|ogg|wav)$"),
    NoOffsetPlayer("speexdec %s",
            r"\.spx$"),
    ]

if __name__ == "__main__":
    main()
