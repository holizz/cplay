Description
-----------

``cplay`` is a minimalist music player with a textual user interface
written in Python. It aims to provide a power-user-friendly interface
with simple filelist and playlist control.
Instead of building an elaborate database of your music library,
``cplay`` allows you to quickly browse the filesystem and enqueue
directories.

Dependencies
------------

- `Python 2.6+ <http://www.python.org/>`_

Music players supported (one of the first two provides support for most
formats):

- various formats: `mplayer <http://www.mplayerhq.hu/>`_,
  `gst123 <http://space.twc.de/~stefan/gst123.php>`_
- mp3: `mpg321 <http://sourceforge.net/projects/mpg321/>`_,
  `mpg123 <http://www.mpg123.org/>`_,
  `madplay <http://www.mars.org/home/rob/proj/mpeg/>`_
- ogg, flac: `ogg123 <http://www.vorbis.com/>`_
- various: `splay <http://splay.sourceforge.net/>`_,
  `mikmod <http://www.mikmod.org/>`_
  `xmp <http://xmp.sf.net/>`_,
  `sox <http://sox.sf.net/>`_,
  `speex <http://www.speex.org/>`_

Other optional components:

-  reading metadata (tags):

   -  `mutagen <https://bitbucket.org/lazka/mutagen>`_

-  volume control:

   -  `alsaaudio <http://pyalsaaudio.sourceforge.net/>`_
   -  ``pulseaudio-utils``, specifically the ``pactl`` command.

Installation
------------

::

    $ make install

In Debian/Ubuntu, the following install a selection of players and optional components::

    $ sudo apt-get install mplayer gst123 mpg321 vorbis-tools mikmod xmp speex sox python-alsaaudio pulseaudio-utils

Usage
-----

::

    $ cplay [-nrRv] [ file | dir | playlist ] ...

When in doubt, press ``h`` for a friendly help page.

Configuration
-------------

If you would like to change options passed to the actual players just
edit the ``PLAYERS`` list at the end of the cplay script.

Miscellaneous
-------------

A playlist can contain URLs, but the playlist itself will have to be
local. For mpeg streaming, ``splay`` is recommended.

It is also possible to pipe a playlist to ``cplay``, as stdin will be
reopened on startup unless it is attached to a tty.

Remote control via ``/tmp/cplay-control-$USER`` -- refer to the class ``FIFOControl`` for the list of recognized commands.
