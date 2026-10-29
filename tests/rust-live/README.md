# Live Cargo projects

Real projects from GitHub, pinned to a release and built as they are:
`nix run --file . internal.test-rust-live`. Each builds in three modes (`cli`,
`huggorm`, `recursive`), and the modes must give one binary.

| project | what it covers | needed | changed in pydd |
| --- | --- | --- | --- |
| ripgrep 15.2.0 | a workspace of ten crates, build scripts | `program = "rg"` | nothing |
| fd 10.5.0 | jemalloc, built from C by tikv-jemalloc-sys; `lto = true` | `program = "fd"` | LTO by unit role |
| hyperfine 2.0.0 | `lto = true` | nothing | LTO by unit role |
| bat 0.26.1 | libgit2, oniguruma and zlib through their -sys crates; `lto = true` | nothing | LTO; `LIBRARY_PATH` for a tool |

**LTO.** fd, hyperfine and bat set `lto = true` in their release
profile. Cargo runs LTO where it links a binary, and the libraries it
links keep their bitcode. The planner passes the same flags by the role
of each unit.

**`LIBRARY_PATH`.** libz-sys probes for a system zlib by linking a test
program with `-lz`. The node gave the compiler the headers of each tool
and no library path, so the probe failed, and libz-sys built its own
zlib. gcc reads `LIBRARY_PATH` for `-l`, so each tool's `lib` is there
now, and bat links the zlib of nixpkgs.
