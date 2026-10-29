# Live npm projects

Real projects from GitHub, at the releases that nixpkgs builds:
`nix run --file . internal.test-node-live`. Each builds in `cli`, `huggorm` and
`recursive`, and a project with npm's lock also in `out`. The modes must
give one result.

| project | what it covers | needed | changed in pydd |
| --- | --- | --- | --- |
| http-server 14.1.1 | a plain CommonJS program | the commit after the release, which writes a lock of version 3 | nothing |
| nodemon 3.1.14 | a large development tree, from semantic-release | nothing | nothing |
| npm-check-updates 23.0.2 | TypeScript, bundled by vite, declarations by api-extractor | `program = "ncu"` | the project builds outside `node_modules` |
| json-server 1.0.0-beta.3 | TypeScript, compiled by tsc, an ES module | nothing | nothing |
| concurrently 9.2.1 | pnpm 10, `onlyBuiltDependencies`, a member that links to the project | nothing | a link to the project, and scripts in the project only |
| bumpp 12.2.0 | pnpm 11, `allowBuilds`, bundled by tsdown | nothing | nothing |
| changelogen 0.6.2 | pnpm 10, bundled by unbuild | git in the check | nothing |
| diff2html-cli 5.2.15 | Yarn v1, TypeScript by tsc | `program = "diff2html"` | the root's spec as a file |
| gitmoji-cli 9.7.0 | Yarn v1, compiled by babel | `program = "gitmoji"` | the root's spec as a file |

**http-server's release has a lock of version 1**, which names no install
paths. The planner refuses it. nixpkgs applies the next commit of the
project, which writes the lock again, and so does this test.

**nodemon from source says version `0.0.0-development`.** semantic-release
writes the version at publication. So `nodemon --version` names no
release, and the check reads `--help`. nixpkgs' build is the same.

**npm-check-updates failed while the root built in
`$out/lib/node_modules`.** TypeScript and api-extractor skip every file
under a `node_modules` directory, and so skipped the project's own
sources. The root now builds in the build directory, and copies the
result.

**concurrently's `tests` member depends on the project itself**, as
`link:..`. npm names the project by the empty path, and the reader now
does too. Its root also runs `build` in the project only: pnpm, Yarn and
bun run a script there, and npm's `--workspaces` needs npm's own
`workspaces` field.

**Yarn v1 names no programs**, so the root's spec lists every package to
read. With about 750 packages in the lock, the spec passed the 128 KiB that one
environment variable holds. The root now reads its spec from a file
(`passAsFile`).
