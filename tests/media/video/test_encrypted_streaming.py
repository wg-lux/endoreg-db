from endoreg_db.utils.storage_streaming import iter_field_file_bytes
from endoreg_db.utils.storage_streaming import field_file_size


def test_field_file_size_uses_encrypted_storage_plaintext_size():
    payload = b"abcdef"
    storage = FakeEncryptedStorage(payload)
    field_file = FakeFieldFile("video.mp4", storage)

    result = field_file_size(field_file)

    assert result == 6


class FakeEncryptedStorage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.iter_calls: list[tuple[str, int, int, int]] = []

    def get_plaintext_size(self, name: str) -> int:
        return len(self.payload)

    def iter_decrypted_range(
        self, name: str, *, start: int, end: int, chunk_size: int = 64 * 1024
    ):
        self.iter_calls.append((name, start, end, chunk_size))
        yield self.payload[start : end + 1]


class FakeFieldFile:
    def __init__(self, name: str, storage: FakeEncryptedStorage):
        self.name = name
        self.storage = storage

    @property
    def size(self) -> int:
        raise AssertionError("size property should not be used for encrypted storage")

    def open(self, mode: str = "rb"):
        raise AssertionError("open() should not be used for encrypted storage")


def test_iter_field_file_bytes_uses_encrypted_storage_full_range():
    payload = b"abcdef"
    storage = FakeEncryptedStorage(payload)
    field_file = FakeFieldFile("video.mp4", storage)

    result = b"".join(iter_field_file_bytes(field_file, start=0, end=5))

    assert result == payload
    assert storage.iter_calls == [("video.mp4", 0, 5, 64 * 1024)]


def test_iter_field_file_bytes_uses_encrypted_storage():
    payload = b"abcdef"
    storage = FakeEncryptedStorage(payload)
    field_file = FakeFieldFile("video.mp4", storage)

    result = b"".join(iter_field_file_bytes(field_file, start=1, end=3))

    assert result == b"bcd"
    assert storage.iter_calls == [("video.mp4", 1, 3, 64 * 1024)]
