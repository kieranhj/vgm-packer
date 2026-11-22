# Wrapper so that `make` says something useful from PowerShell / Windows Terminal.
# Nothing to build: the packers are Python scripts.
.PHONY: all help
all: help

help:
	@echo Nothing to build here: vgmpacker.py (.vgc) and vgipacker.py (.vgi) are Python 3 scripts.
	@echo   python vgipacker.py tune.vgm -o tune.vgi      v3 is the default format
	@echo   python vgmpacker.py tune.vgm -o tune.vgc
	@echo   python vgmdump.py tune.vgm                    dump a VGM
	@echo The player is in ..\vgm-player-bbc. See START_HERE.md, README.md and docs\vgi-format.md.
