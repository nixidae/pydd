{
  callPackage,
  nixvenv,
  graphviz,
}:
callPackage ./shared/sphinx-site.nix { inherit nixvenv; } {
  name = "pydd";
  root = ../.;
  tools = [ graphviz ];
}
