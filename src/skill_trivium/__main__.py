"""Provide the ``python -m skill_trivium`` entry point.

Execution is delegated to the same Typer application exposed by the
installed ``trv`` console script so both invocation styles behave alike.
"""

from skill_trivium.cli import main

if __name__ == "__main__":
    main()
