# Contributing

Thanks for taking a look. Mint-Gate is a portfolio project, but issues and pull requests are welcome.

## Setup

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
cd Blockchain; npm ci; cd ..
.\venv\Scripts\pre-commit install      # ruff + prettier on every commit
```

## Before opening a pull request

```powershell
.\venv\Scripts\python.exe -m pytest -m "not slow"   # fast suite (CI runs this)
.\venv\Scripts\python.exe -m pytest -m slow         # real models; needs the CLIP download
cd Blockchain; npx hardhat test; npm run coverage    # coverage must stay >= 95 %
```

- One logical change per commit, with a message that says *why*.
- New contract features need Hardhat tests for every path and revert, and a Slither run
  (`slither .` in `Blockchain/`). Explain any accepted finding in `docs/security.md`.
- Detection changes must be benchmarked out-of-source (see the model-selection rules in `CLAUDE.md`)
  and report metrics per domain. Held-out numbers only.
- Never commit `.env` files, datasets or private keys.

## Layout

See "Repository layout" in `README.md`. Tests live in `backend/tests/` (fixtures in
`backend/tests/fixtures/`) and `Blockchain/test/`.
