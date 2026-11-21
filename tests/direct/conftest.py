"""Shared helpers for direct mode tests."""


def to_hex(addr):
    """Return the checksummed hex form of bytes or an Address instance.

    Address fixtures return raw bytes when they are created before the GenLayer
    SDK is loaded, so call this only after ``direct_deploy``.
    """
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    from genlayer.py.types import Address

    return Address(addr).as_hex
