"""Incremental reasoning-tag removal, including markers split across native chunks."""

from .contracts import GatewayError


class VisibleText:
    """Retain only short marker tails; hidden bodies never become delivery/history data."""

    def __init__(self) -> None:
        self.tail = ""
        self.closing: str | None = None

    def feed(self, text: str) -> str:
        """Remove explicit think/analysis regions without delaying ordinary text chunks."""
        data = self.tail + text
        self.tail = ""
        visible: list[str] = []
        opening = {"<think>": "</think>", "<analysis>": "</analysis>"}
        while data:
            lowered = data.lower()
            if self.closing is not None:
                position = lowered.find(self.closing)
                if position < 0:
                    self.tail = data[-(len(self.closing) - 1) :]
                    return "".join(visible)
                data = data[position + len(self.closing) :]
                self.closing = None
                continue
            found = [(lowered.find(marker), marker) for marker in opening if marker in lowered]
            if found:
                position, marker = min(found)
                visible.append(data[:position])
                data = data[position + len(marker) :]
                self.closing = opening[marker]
                continue
            suffix = 0
            for marker in opening:
                for length in range(1, len(marker)):
                    if lowered.endswith(marker[:length]):
                        suffix = max(suffix, length)
            if suffix:
                visible.append(data[:-suffix])
                self.tail = data[-suffix:]
            else:
                visible.append(data)
            break
        return "".join(visible)

    def finish(self) -> str:
        """Never publish an incomplete reasoning region or ambiguous opening marker."""
        if self.closing is not None or self.tail:
            raise GatewayError("incomplete_output_channel")
        return ""
