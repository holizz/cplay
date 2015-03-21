#!/usr/bin/env python

from setuptools import setup


with open('cplay/cplay.py') as fh:
    for line in fh:
        if line.startswith('__version__'):
            name, version = line.split('"')[1].split()
            break


setup(
    name=name,
    version=version,
    description="A curses front-end for various audio players",
    long_description=open('README.rst').read(),
    url='https://github.com/andreasvc/cplay',
    author='Ulf Betlehem',
    author_email='flu@iki.fi',
    maintainer='Andreas van Cranenburgh',
    maintainer_email='A.W.vanCranenburgh@uva.nl',
    packages=['cplay'],
    extras_require={
        'metadata': ['mutagen'],
        'alsa mixer': ['pyalsaaudio'],
    },
    entry_points={'console_scripts': [
        'cplay=cplay.cplay:main',
    ]},
    license='GPLv2+',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console :: Curses',
        'Intended Audience :: End Users/Desktop',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'License :: OSI Approved :: GNU General Public License v2 or later '
            '(GPLv2+)',
        'Topic :: Multimedia :: Sound/Audio :: Players',
    ])
