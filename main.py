"""NeuroPlan entry point: python main.py"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

from neuroplan import config


def setup_logging() -> None:
    """Rotating log file in the data folder (1 MB x 3). API keys are never passed to the logger."""
    handler = RotatingFileHandler(config.app_dir() / config.LOG_FILENAME, maxBytes=config.LOG_MAX_BYTES,
                                  backupCount=config.LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def ask_recreate(path: Path) -> bool:
    """The database file is corrupted: ask before renaming it to .bak and starting fresh."""
    import tkinter
    from tkinter import messagebox

    root = tkinter.Tk()
    root.withdraw()
    try:
        return bool(messagebox.askyesno(
            config.APP_NAME,
            f"The data file is damaged and cannot be opened:\n{path}\n\n"
            f"Rename it to {path.name}.bak and start with a fresh database?"))
    finally:
        root.destroy()


def main() -> int:
    from neuroplan.ui.dpi import enable_dpi_awareness
    enable_dpi_awareness()          # before any Tk window, also in the exe (see neuroplan/ui/dpi.py)
    setup_logging()
    log = logging.getLogger("neuroplan")
    from neuroplan.db.repository import open_database
    from neuroplan.seed import seed_if_first_launch
    from neuroplan.ui.app import NeuroPlanApp

    if config.is_frozen():
        try:
            moved = config.migrate_legacy_db(config.install_dir(), config.app_dir())
            if moved is not None:
                log.info("copied the database of an older build to %s", moved)
        except OSError:
            log.exception("could not copy the old database next to the exe")
    try:
        repo = open_database(config.default_db_path(), ask_recreate)
    except sqlite3.DatabaseError:
        log.error("database is corrupted and was not recreated")
        return 1
    try:
        seed_if_first_launch(repo, date.today())
        app = NeuroPlanApp(repo)
        app.mainloop()
    except Exception:
        log.exception("fatal error")
        raise
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
