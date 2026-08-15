"""PickHero application entry point."""

import sys
from pathlib import Path

from pickhero.config import Config


def _resolve_songs_dir(config: Config) -> None:
    """Fix songs_dir for frozen exe: look next to the executable."""
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path.cwd()

    songs_path = Path(config.songs_dir)
    if not songs_path.is_absolute():
        config.songs_dir = str(base_dir / songs_path)


def _apply_songs_argument(config: Config, argv: list[str]) -> None:
    """Handle --songs <path>, and remember it.

    songs_dir defaults to "songs" relative to wherever the app was started
    from, which is invisible and surprising when your tabs live elsewhere.
    Storing an absolute path makes the choice stick across launches.
    """
    if "--songs" not in argv:
        return
    idx = argv.index("--songs")
    if idx + 1 >= len(argv):
        print("--songs needs a folder, e.g. --songs \"C:\\Users\\me\\Tabs\"")
        return
    folder = Path(argv[idx + 1]).expanduser()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return
    config.songs_dir = str(folder.resolve())
    config.save()
    print(f"Songs folder set to: {config.songs_dir}")


def main():
    if "--console" in sys.argv:
        # Phase 1 console demo for audio testing
        from pickhero.audio.input import run_console_demo
        try:
            run_console_demo()
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)
    else:
        config = Config.load()
        _apply_songs_argument(config, sys.argv)
        _resolve_songs_dir(config)
        print(f"Songs folder: {config.songs_dir}")
        from pickhero.ui.app import App
        App(config=config).run()


if __name__ == "__main__":
    main()
