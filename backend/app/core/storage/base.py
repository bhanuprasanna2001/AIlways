from typing import Protocol


class FileStore(Protocol):
    """Protocol for file storage backends.

    Implementations must support save, get, delete, and exists operations.
    All paths are relative to the store's root directory.
    """

    async def save(self, path: str, content: bytes) -> str:
        """Save content to the given path.

        Args:
            path: Relative path within the store.
            content: Raw bytes to write.

        Returns:
            str: The path where the file was saved.
        """
        ...

    async def get(self, path: str) -> bytes:
        """Read content from the given path.

        Args:
            path: Relative path within the store.

        Returns:
            bytes: The raw file content.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        ...

    async def delete(self, path: str) -> None:
        """Delete the file at the given path.

        Args:
            path: Relative path within the store.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        ...

    async def exists(self, path: str) -> bool:
        """Check whether a file exists at the given path.

        Args:
            path: Relative path within the store.

        Returns:
            bool: True if the file exists.
        """
        ...
