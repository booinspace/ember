"""Enforce coding prompt admission after Strata renders text and expands image tokens."""

import sys
from pathlib import Path
from typing import Protocol, cast


class _Prepare(Protocol):
    def __call__(
        self, service: object, *args: object, **kwargs: object
    ) -> tuple[list[int], bool, int]: ...


def main() -> int:
    """Install the coding-only guard and invoke the pinned runner's normal entry point."""
    script = Path(sys.argv.pop(1)).resolve(strict=True)
    preemption = sys.argv[1:2] == ["--ember-preemption"]
    if preemption:
        sys.argv.pop(1)
    sys.argv[0] = str(script)
    sys.path.insert(0, str(script.parent.parent))
    from serve import server  # type: ignore[import-not-found]

    module_file = cast(str, server.__file__)  # type: ignore[reportUnknownMemberType]
    if Path(module_file).resolve() != script:
        raise RuntimeError("Loaded Strata server does not match verified profile.")
    prepare = cast(_Prepare, server.Service.prepare)  # type: ignore[reportUnknownMemberType]
    overflow = cast(type[ValueError], server.ContextOverflow)  # type: ignore[reportUnknownMemberType]

    def checked(service: object, *args: object, **kwargs: object) -> tuple[list[int], bool, int]:
        request = kwargs.get("req")
        limit: object = (
            cast(dict[str, object], request).get("ember_coding_input_limit")
            if isinstance(request, dict)
            else None
        )
        if type(limit) is not int:
            return prepare(service, *args, **kwargs)
        try:
            ids, thinking, maximum = prepare(service, *args, **kwargs)
        except overflow:  # Pinned Strata: prepare has not started generation.
            raise ValueError("ember_coding_context_length_exceeded") from None
        if len(ids) > limit:
            raise ValueError("ember_coding_context_length_exceeded")
        return ids, thinking, maximum

    server.Service.prepare = checked  # type: ignore[reportUnknownMemberType]
    if preemption:
        # The isolated Python invocation excludes the wrapper directory from sys.path.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from strata_preemption import StrataServer, install  # type: ignore[import-not-found]

        install(cast(StrataServer, server))
    return cast(int, server.main())  # type: ignore[reportUnknownMemberType]


if __name__ == "__main__":
    sys.exit(main())
