# REACHER — Operant Experiment Control Server

**Hardware control, session coordination, and live data capture for Arduino-driven rodent operant behavior experiments.**

[![Version](https://img.shields.io/badge/version-3.4.0--alpha.8-blue)](https://github.com/Otis-Lab-MUSC/reacher/releases)
[![Language](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Unlicense-green)](LICENSE)
[![Changelog](https://img.shields.io/badge/changelog-CHANGELOG.md-orange)](CHANGELOG.md)
[![Phoxel Workbench](https://img.shields.io/badge/Phoxel_Workbench-member-orange)](https://github.com/Otis-Lab-MUSC)

*Written by*: Joshua Boquiren

[![](https://img.shields.io/badge/@thejoshbq-grey?style=flat&logo=github)](https://github.com/thejoshbq)

---

## Overview

REACHER runs rodent operant conditioning experiments end to end — it drives the Arduino-based behavioral chamber, records every event as it happens, and exports the session ready for analysis. Paradigms ship as firmware for fixed ratio, progressive ratio, variable interval, omission, and Pavlovian conditioning, each exposing the hardware a self-administration rig actually uses: levers, cue lights and speakers, syringe pumps, lick circuits, and optogenetic laser control. Reinforcement ratios, timing parameters, session limits, and per-board pin assignments are set from the interface, so a rig is reconfigured between subjects without editing or recompiling firmware.

Every lever press, infusion, lick, and cue is timestamped against the trial clock and streamed live to the browser while the session runs, alongside microscope frame timestamps for aligning behavior with calcium imaging. A single host coordinates several chambers at once — each with its own serial port, paradigm, and output file — and hosts on the same network can be paired so one browser drives an entire rig room. Firmware for any paradigm is flashed onto a board directly from the interface, with no separate Arduino toolchain on the experimenter's machine.

---

## Getting Started

```bash
pip install reacher2p                               # from PyPI
pip install reacher2p-3.4.0a8-py3-none-any.whl        # from a release wheel
```

Host setup — installation, pairing, and systemd services — is documented in [`docs/setup-guide.md`](docs/setup-guide.md); see [CONTRIBUTING.md](CONTRIBUTING.md) and [RELEASING.md](RELEASING.md) for development and release process.

---

## Architecture & Dependencies

| Component | Language | Framework / Libraries |
|---|---|---|
| REST & WebSocket API | Python 3.10+ | FastAPI, Uvicorn, websockets |
| Serial kernel | Python 3.10+ | pyserial |
| Session manager | Python 3.10+ | Python standard library (threading, asyncio) |
| Discovery & pairing | Python 3.10+ | zeroconf, httpx |
| Terminal monitor | Python 3.10+ | Rich, httpx |
| Firmware uploader | Python 3.10+ | avrdude |
| Arduino firmware | C++ (Arduino) | REACHERDevices, arduino-cli (AVR core) |

---

## License

This project is released into the public domain under the Unlicense. See [LICENSE](LICENSE) for details.

## Contact

Joshua Boquiren — [thejoshbq@proton.me](mailto:thejoshbq@proton.me)

[GitHub: Otis-Lab-MUSC/reacher](https://github.com/Otis-Lab-MUSC/reacher)
