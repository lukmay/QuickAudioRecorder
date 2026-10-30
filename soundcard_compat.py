"""Work around undersized Windows PROPVARIANT allocations in SoundCard."""
import sys


def ensure_propvariant_allocation(backend):
    """Patch only affected CFFI layouts; keep COM ownership with SoundCard.

    The PROPVARIANT union must fit a BLOB (length plus pointer), not only
    a pointer. On 64-bit Windows these layouts occupy 24 and 16 bytes.
    GetValue writes a full native PROPVARIANT even for a string property.
    """
    ffi = backend._ffi
    size = max(ffi.sizeof("PROPVARIANT"), ffi.sizeof("BLOB_PROPVARIANT"))
    if ffi.sizeof("PROPVARIANT") >= size:
        return False
    variant = backend._PropVariant
    if getattr(variant, "_qar_safe_allocation", False):
        return False

    class SafePropVariant:
        _qar_safe_allocation = True

        def __init__(self):
            self.ptr = ffi.NULL
            raw = backend._ole32.CoTaskMemAlloc(size)
            if raw == ffi.NULL:
                raise MemoryError("Could not allocate Windows PROPVARIANT")
            # COM expects an initialized empty variant on error paths too.
            ffi.buffer(raw, size)[:] = bytes(size)
            self.ptr = ffi.cast("PROPVARIANT *", raw)

        def __del__(self):
            ptr = getattr(self, "ptr", ffi.NULL)
            if ptr != ffi.NULL:
                self.ptr = ffi.NULL
                try:
                    try:
                        backend._ole32.PropVariantClear(ptr)
                    finally:
                        backend._ole32.CoTaskMemFree(ptr)
                except Exception:
                    # Destructors must remain safe during interpreter teardown.
                    pass

    backend._PropVariant = SafePropVariant
    return True


def apply_soundcard_compat():
    if sys.platform == "win32":
        from soundcard import mediafoundation

        ensure_propvariant_allocation(mediafoundation)
