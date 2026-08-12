# GitDeck

A fast, **offline**, **100% open-source** visual Git client for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/git-deck).

> **100% AI-built and open source.** Apache-2.0.

## What it does

A clean Git GUI: stage and commit with a diff view, create/switch/merge branches, browse commit history as a graph, view and revert changes, and manage remotes — all on top of a pure-Python Git implementation. Works on any local repository.

## Install

Download **`GitDeck-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/git-deck) or the [GitHub release](https://github.com/quickpod/git-deck/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python git_deck_app.py          # GUI
python -m gitdeck --help    # CLI
```


## Features

Everything runs on pure-Python [dulwich](https://www.dulwich.io/) — no system `git` binary is required.

- **Changes** — see the working-tree status (staged / unstaged / untracked), stage and unstage files, review a colourised diff, and commit with a message and author.
- **History** — browse commits as a list with a simple commit graph (parent-edge lanes); select any commit to see exactly what it changed.
- **Branches** — list, create, checkout, merge (fast-forward or 3-way, with clear conflict reporting) and delete local branches.
- **Remotes** — list and add remotes, then fetch / pull / push (network operations run on a background thread in the GUI and never block the UI).
- **Comfort** — light/dark QuickOpen theme, recent-repository list, and a matching command-line interface for scripting.

The GUI (`gitdeck.gui`) and CLI (`gitdeck.__main__`) are thin front-ends over the tested `gitdeck` library; none of them re-implement Git logic.

## CLI examples

```sh
# Create a repository and make the first commit
python -m gitdeck init ./demo
echo "hello" > demo/readme.txt
python -m gitdeck -C ./demo stage readme.txt
python -m gitdeck -C ./demo status
python -m gitdeck -C ./demo commit -m "Initial commit" --author "Ada <ada@example.com>"
python -m gitdeck -C ./demo log

# Review changes
python -m gitdeck -C ./demo diff readme.txt          # working-tree diff
python -m gitdeck -C ./demo diff --staged            # staged diff
python -m gitdeck -C ./demo diff --commit <sha>      # a commit's diff

# Branches and merging
python -m gitdeck -C ./demo branch create feature
python -m gitdeck -C ./demo branch checkout feature
python -m gitdeck -C ./demo branch list
python -m gitdeck -C ./demo merge feature --author "Ada <ada@example.com>"
python -m gitdeck -C ./demo branch delete feature

# Remotes
python -m gitdeck -C ./demo remote add origin https://example.com/repo.git
python -m gitdeck -C ./demo remote list
```

`-C/--path` selects the repository (default: the current directory). Any failure prints `error: <message>` and exits non-zero — never a traceback.

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
