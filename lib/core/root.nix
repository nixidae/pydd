# The root of a planned build, as an ordinary derivation (nixidae/nixidae#788).
#
# **`builtins.outputOf` gives a bare string, and a string is not a package.**
# It has no `meta`, no `override`, no `overrideAttrs` and no hook. Measured:
# `nix run` over that string fails with `attribute 'type' does not exist`, and
# an attribute set that names `outPath` and `drvPath` by hand fails with `path
# "/0a988..." is not in the Nix store`. `docs/NIX-FACTS.md` holds both, under
# "A dynamic output carries no `meta`".
#
# So the root is a `stdenvNoCC.mkDerivation` whose output is a tree of symbolic
# links into the dynamic output. Its directories are its own, so `postInstall`
# and `overrideAttrs` can add or remove files. A virtual environment works
# through the links: Python finds `pyvenv.cfg` beside the `bin/python` that it
# was started as, and not beside the target of that link.
#
# **The output of this derivation is deferred, like every node of the graph.**
# Its input is the dynamic output, whose path no evaluation knows. `nix run`
# realises the installable and then reads the result, so it works.
# `lib.getExe` gives a placeholder, which is correct inside another derivation.
#
# **The output floats too** (nixidae/nixidae#795). The `.drv` names the planner,
# and each mode has its own planner. An input-addressed root would get a path
# for each mode around one environment. A floating root gets the path of
# its links, so every mode that gives one environment gives one root.
#
# **The build verifies the program rather than trust the name.** A member
# usually installs a console script of its own name, and a member that does not
# is why `program` is an argument at all.
#
# Each ecosystem calls this for its result: `python/uv.nix`, `python/poetry.nix`
# and `rust/default.nix`.
{
  pkgs,
  # The dynamic output, as `builtins.outputOf <planner>.outPath "out"`.
  application,
  # The program that `meta.mainProgram` names, or `null` for no `meta`.
  program ? null,
  # The name of the derivation. `program` takes this place when it is not null,
  # because the name of a program is the better name for a result.
  name,
  passthru ? { },
}:

pkgs.stdenvNoCC.mkDerivation {
  name = if program != null then program else name;
  inherit application passthru;
  __contentAddressed = true;
  outputHashMode = "recursive";
  outputHashAlgo = "sha256";
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  # Fixup runs, so that `wrapGAppsHook3` and a `wrapProgram` in `postFixup`
  # wrap the programs (nixidae/nixidae#745). Each step that rewrites a file in
  # place is off: every file here is a link into another, read-only, store path.
  dontStrip = true;
  dontPatchELF = true;
  dontPatchShebangs = true;
  dontGzipMan = true;
  dontPruneLibtoolFiles = true;
  installPhase = ''
    runHook preInstall
    ${pkgs.lib.optionalString (program != null) ''
      if [ ! -x "$application/bin/${program}" ]; then
        echo "the application holds no program called '${program}'." >&2
        echo "It holds:" >&2
        ls -1 "$application/bin" >&2
        echo "Name one of those with the 'mainProgram' argument." >&2
        exit 1
      fi
    ''}
    cp -rs --no-preserve=mode "$application" "$out"
    runHook postInstall
  '';
  meta = pkgs.lib.optionalAttrs (program != null) { mainProgram = program; };
}
