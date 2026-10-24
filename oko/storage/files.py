"""Durable local-file operations used by independent media downloads."""
import hashlib
import os
from pathlib import Path


def file_digest(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while block:=source.read(1048576):
            digest.update(block)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    fd=os.open(path,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
