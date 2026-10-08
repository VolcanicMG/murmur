"""At-rest encryption for history and kept recordings: Windows DPAPI (CurrentUser), so the files only open under the Windows
login that wrote them. No win32 at import; where DPAPI is missing (Linux dev/tests) seal() writes plaintext."""
import base64, functools

MAGIC = b"MURMUR-SEALED-1\n"
_ENTROPY = b"murmur at-rest v1"
_UI_FORBIDDEN = 0x1  # CRYPTPROTECT_UI_FORBIDDEN


class SealedError(RuntimeError):
    pass


@functools.cache
def available():
    """True when DPAPI (pywin32's win32crypt) can be used."""
    try:
        import win32crypt  # noqa: F401
        return True
    except ImportError:
        return False


def _protect(data):
    import win32crypt
    return win32crypt.CryptProtectData(data, "murmur", _ENTROPY, None, None, _UI_FORBIDDEN)


def _unprotect(blob):
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, _ENTROPY, None, None, _UI_FORBIDDEN)[1]


def is_sealed(data):
    return data.startswith(MAGIC)


def seal(data):
    """MAGIC + DPAPI blob of `data`; `data` unchanged where DPAPI is unavailable or it is already sealed."""
    if is_sealed(data) or not available():
        return data
    return MAGIC + _protect(data)


def unseal(data):
    """Plaintext of a sealed blob; anything without MAGIC (written before sealing) is returned as is.

    Raises:
        SealedError: sealed data but no DPAPI here (e.g. Linux).
        ValueError: DPAPI refused it (torn/corrupt blob, or another Windows user's)."""
    if not is_sealed(data):
        return data
    if not available():
        raise SealedError("sealed by murmur on Windows; run with the Windows venv")
    try:
        return _unprotect(data[len(MAGIC):])
    except Exception as e:
        raise ValueError(f"can't unseal (corrupt, or another Windows user's): {e}") from e


def seal_line(text):
    """One JSONL line, sealed: "!" + base64(seal(text)); the plain text where DPAPI is unavailable."""
    data = seal(text.encode("utf-8"))
    return "!" + base64.b64encode(data).decode("ascii") if is_sealed(data) else text


@functools.cache  # each sealed line decrypts once per process (ponytail: grows with history; bound it if history gets huge)
def _open_sealed(line):
    return unseal(base64.b64decode(line[1:])).decode("utf-8")


def open_line(line):
    """Plaintext of a seal_line() line; plain lines ("{...") pass through. Raises like unseal(); a torn base64 line raises ValueError."""
    return _open_sealed(line) if line.startswith("!") else line
