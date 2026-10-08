"""Cancellable terminal input without a blocked executor worker on Windows."""

import asyncio
import os
import sys


async def read_line(prompt: str) -> str:
    """Read Windows console keys cooperatively; redirected input uses a bounded worker."""
    if os.name != "nt" or not sys.stdin.isatty():
        return await asyncio.to_thread(input, prompt)
    import msvcrt

    print(prompt, end="", flush=True)
    characters: list[str] = []
    while True:
        if not msvcrt.kbhit():
            await asyncio.sleep(0.05)
            continue
        value = msvcrt.getwch()
        if value in {"\x00", "\xe0"}:
            msvcrt.getwch()
        elif value in {"\r", "\n"}:
            print()
            return "".join(characters)
        elif value == "\x03":
            raise asyncio.CancelledError()
        elif value == "\x1a":
            raise EOFError()
        elif value == "\b":
            if characters:
                characters.pop()
                print("\b \b", end="", flush=True)
        elif len(characters) < 16000:
            characters.append(value)
            print(value, end="", flush=True)
