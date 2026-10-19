# What nixpkgs knows about building a Python package from source, as build
# tools for the node that builds it (nixidae/nixidae#784).
#
# nixpkgs already packaged most Python distributions that hold C code, and its
# package names the libraries and the programs that the build needs. A
# planner builds from the archive of the lock file, so it takes those inputs
# and not the nixpkgs build. The hints come from `python.pkgs`, so an overlay
# or `packageOverrides` of the caller changes them too.
{
  lib,
  pkgs,
  python,
}:
let
  inherit (import ../core/tools.nix { inherit lib; }) isHook outputsOf;

  # A Python package, or an interpreter, which the node already has.
  isPython = drv: drv ? pythonModule || drv ? sitePackages;

  # **A Python input of nixpkgs may stand for a program.** The `cmake` and
  # `ninja` of `python.pkgs` are stubs that point at the programs of nixpkgs,
  # because a backend such as scikit-build-core looks for the programs. A
  # Python input whose name is also a top-level package gives that package.
  program =
    drv:
    let
      name = lib.getName drv;
      bare = lib.removePrefix "${python.libPrefix}-" name;
      found = pkgs.${bare} or null;
    in
    lib.optional (found != null && lib.isDerivation found && !(isPython found)) found;

  # **A Python input of nixpkgs may carry the programs it runs.** meson-python
  # propagates `ninja`, so a meson build needs no declaration of it.
  carried =
    drv:
    builtins.filter (input: lib.isDerivation input && !(isHook input) && !(isPython input)) (
      drv.propagatedBuildInputs or [ ]
    );

  hintsOf =
    package:
    let
      inputs =
        package.nativeBuildInputs or [ ]
        ++ package.buildInputs or [ ]
        ++ package.propagatedBuildInputs or [ ];
      native = builtins.filter (drv: lib.isDerivation drv && !(isHook drv) && !(isPython drv)) inputs;
      programs = lib.concatMap (drv: program drv ++ carried drv) (
        builtins.filter (drv: lib.isDerivation drv && isPython drv) inputs
      );
    in
    if native == [ ] && programs == [ ] then
      [ ]
    else
      # stdenv gives every nixpkgs build a compiler, so the package's own list
      # never names one.
      lib.unique ([ pkgs.stdenv.cc ] ++ lib.concatMap outputsOf native ++ programs);
in
/**
  The build tools that nixpkgs implies for each of `names`, by package name.
  A name that `python.pkgs` does not hold, or whose package names no native
  input, gets no entry.
*/
names:
lib.filterAttrs (_: tools: tools != [ ]) (
  lib.genAttrs names (
    name:
    let
      # An alias of `python.pkgs` may throw when it is read.
      tried = builtins.tryEval (
        let
          hints = hintsOf python.pkgs.${name};
        in
        builtins.deepSeq (map lib.getName hints) hints
      );
    in
    if python.pkgs ? ${name} && tried.success then tried.value else [ ]
  )
)
