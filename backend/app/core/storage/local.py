import os
import aiofiles
from pathlib import Path

from app.core.logger import setup_logger

logger = setup_logger(__name__)


class LocalFileStore:
    """Local filesystem implementation of the FileStore protocol.

    All paths are relative to the configured base directory.
    Path traversal and absolute paths are rejected.
    """

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Resolve a relative path against the base directory safely.

        Args:
            path: Relative path within the store.

        Returns:
            Path: Absolute resolved path.

        Raises:
            ValueError: If the path is unsafe (traversal, absolute, null bytes).
        """
        if "\x00" in path:
            raise ValueError("Null bytes not allowed in path")
        if os.path.isabs(path):
            raise ValueError("Absolute paths not allowed")
        if ".." in Path(path).parts:
            raise ValueError("Path traversal not allowed")

        resolved = (self._base / path).resolve()
        if not str(resolved).startswith(str(self._base)):
            raise ValueError("Path escapes base directory")
        return resolved

    # ------------------------------------------------------------------
    # FileStore interface
    # ------------------------------------------------------------------

    async def save(self, path: str, content: bytes) -> str:
        """Save content to the given path.

        Args:
            path: Relative path within the store.
            content: Raw bytes to write.

        Returns:
            str: The relative path where the file was saved.
        """
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)

        tmp = full.with_suffix(full.suffix + ".tmp")
        async with aiofiles.open(tmp, "wb") as f:
            await f.write(content)
        os.rename(tmp, full)

        logger.debug(f"Saved {len(content)} bytes to {path}")
        return path

    async def get(self, path: str) -> bytes:
        """Read content from the given path.

        Args:
            path: Relative path within the store.

        Returns:
            bytes: The raw file content.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        full = self._resolve(path)
        if not full.exists():
            raise FileNotFoundError(f"File not found: {path}")

        async with aiofiles.open(full, "rb") as f:
            return await f.read()

    async def delete(self, path: str) -> None:
        """Delete the file at the given path.

        Args:
            path: Relative path within the store.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        full = self._resolve(path)
        if not full.exists():
            raise FileNotFoundError(f"File not found: {path}")
        full.unlink()
        logger.debug(f"Deleted {path}")

    async def exists(self, path: str) -> bool:
        """Check whether a file exists at the given path.

        Args:
            path: Relative path within the store.

        Returns:
            bool: True if the file exists.
        """
        full = self._resolve(path)
        return full.exists()
