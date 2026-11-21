from dataclasses import dataclass, field

VERSION = '1'
MAX_FILE = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
PARSE_TIMEOUT = 45
MAX_OUTPUT = 8 * 1024 * 1024
MAX_FILES = 100
MAX_BATCH = 200 * 1024 * 1024


class ConversionError(Exception):
    def __init__(self, code, message, *, skipped=False):
        super().__init__(message)
        self.code, self.message, self.skipped = code, message, skipped


@dataclass
class Converted:
    body: str
    title: str = ''
    warnings: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    mapping: list[dict] = field(default_factory=list)
    encoding: str | None = None
    partial: bool = False
