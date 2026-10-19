# /// script
# requires-python = ">=3.13"
# dependencies = ["rich>=15.0.0"]
# ///
"""A PEP 723 script: its dependencies live in the block above, and `hello.py.lock` pins them."""

from rich.text import Text


def main() -> None:
    print(Text("hello from a script").plain)


if __name__ == "__main__":
    main()
