## Shell Environment

<!-- Machine-specific. deploy.sh merges this only when --shell-env is passed
     explicitly; the defaults described below are placeholders, not claims about
     the reader's machine. Edit them for this host, or drop the file. -->

- Interactive shell: **zsh**; config: `~/.zshrc`
- Project aliases live in `~/.zshrc`; source it when a command needs them
- Write terminal one-liners in zsh syntax; do not wrap commands in `bash -lc`
  unless the script is bash-only
- Avoid bash-only constructs (`shopt`, `[[` quirks, `declare`, `BASH_SOURCE`)
  in ad-hoc commands
- ROS/catkin setup files are still named `setup.bash`; `source` them from zsh as
  usual — the filename does not mean you must spawn bash
