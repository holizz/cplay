Description
-----------
cplay is a minimalist music player with a textual user interface
written in Python. It aims to provide a power-user-friendly interface
with simple filelist and playlist control.

Dependencies
------------

- python 2.6+         http://www.python.org/

Music players supported (one of the first two provides support for most formats):

- `mplayer`           http://www.mplayerhq.hu/
- `gst123`            http://space.twc.de/~stefan/gst123.php
- `mpg321`            http://sourceforge.net/projects/mpg321/
- `mpg123`            http://www.mpg123.org/
- `madplay`           http://www.mars.org/home/rob/proj/mpeg/
- `ogg123`            http://www.vorbis.com/
- `splay`             http://splay.sourceforge.net/
- `mikmod`            http://www.mikmod.org/
- `xmp`               http://xmp.sf.net/
- `sox`               http://sox.sf.net/
- `speex`             http://www.speex.org/

Other optional components:

- reading metadata (tags):
  - `mutagen`           https://bitbucket.org/lazka/mutagen
- volume control:
  - `alsaaudio`         http://pyalsaaudio.sourceforge.net/
  - `pulseaudio-utils`  specifically the `pacmd` command.

Installation
------------

$ make install

Usage
-----

$ cplay [-nrRv] [ file | dir | playlist ] ...

When in doubt, press 'h' for a friendly help page.

Configuration
-------------
If you would like to change options passed to the actual players
just edit the PLAYERS list in the cplay script.

Miscellaneous
-------------
A playlist can contain URLs, but the playlist itself will
have to be local. For mpeg streaming, splay is recommended.

It is also possible to pipe a playlist to cplay, as stdin
will be reopened on startup unless it is attached to a tty.

Remote control via /tmp/cplay-control-$USER; see lircrc.

