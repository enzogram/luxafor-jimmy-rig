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
