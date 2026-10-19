# What a node reads of a nixpkgs package, for every ecosystem's hints.
#
# A node runs no stdenv, so no setup hook follows `.dev` to `.out` for it,
# and a hook is no input at all.
{ lib }:
rec {
  isHook = drv: lib.hasSuffix "-hook" (lib.getName drv) || lib.hasSuffix "-hook.sh" (lib.getName drv);

  # **An input of nixpkgs is the output stdenv chose.** `buildInputs` of
  # pyyaml holds `libyaml.dev`, and `lib.getLib` returns a chosen output as it
  # is, so pyyaml got no `-L` for `libyaml.so` and fell back to pure Python.
  # This reads the outputs of the package, whatever output the input names.
  outputOf =
    names: drv:
    let
      found = builtins.filter (name: drv ? ${name}) names;
    in
    if found == [ ] then drv else drv.${builtins.head found};

  # **stdenv follows propagated inputs to the end, and a node must too.**
  # gtk3 propagates pango, cairo and gdk-pixbuf, and they propagate glib.
  # `gdk-3.0.pc` requires their `.pc` files, so with gtk3 alone pkg-config
  # reports `gdk-3.0` as missing. This is stdenv's `findInputs`, as a list.
  withPropagated =
    drvs:
    let
      propagated =
        drv:
        builtins.filter (input: lib.isDerivation input && !(isHook input)) (
          lib.flatten (drv.propagatedBuildInputs or [ ] ++ drv.propagatedNativeBuildInputs or [ ])
        );
      item = drv: {
        key = drv.outPath;
        inherit drv;
      };
    in
    map (entry: entry.drv) (
      builtins.genericClosure {
        startSet = map item drvs;
        operator = entry: map item (propagated entry.drv);
      }
    );

  # Each output that a build reads: headers, libraries and programs.
  outputsOf =
    drv:
    lib.unique [
      (outputOf [ "dev" "out" ] drv)
      (outputOf [ "lib" "out" ] drv)
      (outputOf [ "bin" "out" ] drv)
    ];
}
