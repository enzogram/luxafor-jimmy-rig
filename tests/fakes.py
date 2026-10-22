from luxstatus.state import Resolution


class FakeLight:
    """Records what the engine asks the light to do, so tests run without hardware."""

    def __init__(self) -> None:
        self.shown: list[Resolution] = []
        self.ticks = 0

    def show(self, resolution: Resolution) -> None:
        self.shown.append(resolution)

    def tick(self) -> None:
        self.ticks += 1


class FakeHidDevice:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.fail = False  # simulate an unplugged Flag behind a stale handle
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.fail:
            return -1
        self.writes.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Hands out a new FakeHidDevice per open, or raises while ``present`` is False."""

    def __init__(self) -> None:
        self.present = True
        self.opened: list[FakeHidDevice] = []

    def __call__(self) -> FakeHidDevice:
        if not self.present:
            raise OSError("open failed")
        dev = FakeHidDevice()
        self.opened.append(dev)
        return dev
