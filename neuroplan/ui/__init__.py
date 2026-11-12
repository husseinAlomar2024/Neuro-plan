"""The CustomTkinter UI. Importing it makes the process DPI aware first (before any Tk window)."""
from .dpi import enable_dpi_awareness

enable_dpi_awareness()
