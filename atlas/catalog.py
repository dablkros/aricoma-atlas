"""Versioned local extensions to the pinned upstream Device Type catalog."""

from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_DEVICE_TYPES = ROOT / "catalog" / "device-types"


def local_device_type_files(vendor=None, root=LOCAL_DEVICE_TYPES):
    root = Path(root)
    search_root = root / vendor if vendor else root
    if not search_root.exists():
        return []
    pattern = "*" if vendor else "*/*"
    return sorted(
        path
        for path in search_root.glob(pattern)
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    )


def local_device_types_digest(root=LOCAL_DEVICE_TYPES):
    root = Path(root)
    digest = sha256()
    for path in local_device_type_files(root=root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
