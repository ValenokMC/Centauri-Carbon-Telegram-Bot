# Third-party notices

## Runtime: none

This application has **no third-party runtime dependencies**. It uses only the
Python standard library, and the release archive contains this project's own
source and nothing else.

There is therefore nothing to audit here for the shipped product, and no package
on any index that could be swapped out from under you.

## Python

The program needs a Python interpreter, which the user installs themselves.
Python is distributed under the
[PSF License Agreement](https://docs.python.org/3/license.html). Python is not
bundled in the release archive.

## Development only

| Component | Used for | License |
|---|---|---|
| [pytest](https://pytest.org/) | Running the test suite | MIT |

Listed in `requirements-dev.txt`. Not shipped.

## Protocol

The SDCP command codes and message shapes were determined by reading the
Centauri Carbon's own web interface — served by the printer to its owner — and
by observing the printer's replies. No Elegoo source code is included, copied,
or redistributed in this project.

## Trademarks

"Elegoo" and "Centauri Carbon" are trademarks of their respective owner, used
here descriptively: to state which printer this software talks to.

This project is **not affiliated with, endorsed by, or supported by Elegoo**.
Nothing here is an official Elegoo product, and no support commitment from
Elegoo is implied.

"Telegram" is a trademark of Telegram FZ-LLC. This project uses the public
Telegram Bot API as any bot does, and is not affiliated with Telegram.

## This project

Everything in `src/`, `tests/`, `tools/`, `docs/`, `examples/` and `assets/` was
written for this project and is licensed under [MIT](LICENSE).
