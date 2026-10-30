"""SoundCard's Windows property buffer must fit the native union."""
import types

import cffi
import pytest

from soundcard_compat import ensure_propvariant_allocation


def backend_for():
    ffi = cffi.FFI()
    ffi.cdef('typedef struct { unsigned short h[4]; void *data; } PROPVARIANT; '
             'typedef struct { unsigned short h[4]; unsigned long long data[2]; } BLOB_PROPVARIANT;')
    buffers = []
    cleared, freed = [], []
    sizes = []

    def allocate(size):
        sizes.append(size)
        buf = ffi.new('char[]', size)
        ffi.buffer(buf, size)[:] = b'\xff' * size
        buffers.append(buf)
        return buf

    backend = types.SimpleNamespace(
        _ffi=ffi, _PropVariant=type('OriginalVariant', (), {}),
        _ole32=types.SimpleNamespace(CoTaskMemAlloc=allocate,
                                    PropVariantClear=cleared.append,
                                    CoTaskMemFree=freed.append))
    return backend, sizes, cleared, freed


def test_allocates_zeroed_native_size_and_frees_once():
    backend, sizes, cleared, freed = backend_for()
    assert ensure_propvariant_allocation(backend)
    assert not ensure_propvariant_allocation(backend)
    variant = backend._PropVariant()
    assert sizes == [backend._ffi.sizeof('BLOB_PROPVARIANT')]
    assert bytes(backend._ffi.buffer(variant.ptr, sizes[0])) == bytes(sizes[0])
    variant.__del__()
    variant.__del__()
    assert len(cleared) == len(freed) == 1


def test_allocation_failure_is_safe_to_destroy():
    backend, _, cleared, freed = backend_for()
    backend._ole32.CoTaskMemAlloc = lambda size: backend._ffi.NULL
    ensure_propvariant_allocation(backend)
    with pytest.raises(MemoryError):
        backend._PropVariant()
    assert not cleared and not freed


def test_clear_exception_still_frees_storage():
    backend, _, _, freed = backend_for()

    def fail(ptr):
        raise RuntimeError("cleanup failed")

    backend._ole32.PropVariantClear = fail
    ensure_propvariant_allocation(backend)
    variant = backend._PropVariant()
    variant.__del__()
    variant.__del__()
    assert len(freed) == 1


def test_corrected_layout_is_not_patched():
    backend, _, _, _ = backend_for()
    original = backend._PropVariant
    real_ffi = backend._ffi
    backend._ffi = types.SimpleNamespace(sizeof=lambda name: real_ffi.sizeof('BLOB_PROPVARIANT'))
    assert not ensure_propvariant_allocation(backend)
    assert backend._PropVariant is original
