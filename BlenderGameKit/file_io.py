"""Content-aware publication of generated binary assets, independent of Blender."""
from contextlib import contextmanager
import os
import shutil
import tempfile


def files_equal(first, second, chunk_size=1024 * 1024):
    """Compare bytes, not timestamps: edited assets may keep their size and mtime."""
    try:
        if os.path.getsize(first) != os.path.getsize(second):
            return False
        with open(first, "rb") as left, open(second, "rb") as right:
            while True:
                block = left.read(chunk_size)
                if block != right.read(chunk_size):
                    return False
                if not block:
                    return True
    except FileNotFoundError:
        return False


@contextmanager
def staged_output(path):
    """Encode beside the destination; publish complete changed output atomically.

    A dot-prefixed temporary file avoids Godot importing partial assets. Keep
    the real suffix for image encoders which choose their format from it.
    The caller must finish writing before leaving the context. On any failure,
    the old asset stays intact and only our own temporary file is removed.
    """
    folder = os.path.dirname(os.fspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    descriptor, staged = tempfile.mkstemp(
        prefix=".gk_tmp_", suffix=os.path.splitext(path)[1], dir=folder)
    os.close(descriptor)
    try:
        yield staged
        if not files_equal(staged, path):
            os.replace(staged, path)
    finally:
        try:
            os.unlink(staged)
        except FileNotFoundError:
            pass


def copy_if_changed(source, target):
    """Copy a binary asset without changing an identical destination's mtime."""
    if not files_equal(source, target):
        with staged_output(target) as staged:
            shutil.copyfile(source, staged)
    return target
