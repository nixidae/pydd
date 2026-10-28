# The nodes of an npm lock, made on the host, for `plannerProtocol = "out"`.
#
# **On released Nix a planner writes one derivation**, its root, so every
# node that the root names must exist before the planner runs, as an input of
# it. This file makes each node that `src/pydd/node/_npm.py` would write:
# the same name, the same environment, the same JSON in `PYDD_NPM` and the
# same inputs, so the two give one `.drv`. `OutWriter` refuses a root that
# names a node the host did not make, so a difference fails the build.
#
# It is the planner's resolution, a second time, in Nix. The npm lock names
# every node, so the host can. The Rust lane has no such file: only cargo, in
# a build, knows the units.
{
  lib,
  system,
  # The lock file, as `lib.importJSON` reads it.
  lock,
  # The planner's menu, with each store path carrying its context.
  menu,
}:
let
  inherit (builtins)
    attrNames
    elem
    filter
    genericClosure
    hasAttr
    head
    length
    match
    replaceStrings
    split
    substring
    toJSON
    ;
  inherit (lib)
    concatMap
    concatStringsSep
    filterAttrs
    findFirst
    hasPrefix
    last
    listToAttrs
    mapAttrs
    optionalAttrs
    range
    removePrefix
    sort
    splitString
    take
    unique
    ;

  inherit (lock) packages;
  entryOf = path: packages.${path};

  # `pydd.node._npm` has each of these, under the same name.

  isNested = path: match ".*node_modules/.*" path != null;

  name_of =
    path: entry:
    if !isNested path then
      entry.name or (last (splitString "/" path))
    else
      last (filter builtins.isString (split "node_modules/" path));

  # The characters of a store name, as `[A-Za-z0-9+._?=-]`.
  store_name =
    name:
    lib.stringAsChars (char: if match "[A-Za-z0-9+._?=-]" char != null then char else "-") (
      removePrefix "@" name
    );

  algorithms = [
    "sha512"
    "sha384"
    "sha256"
    "sha1"
  ];
  strongest =
    integrity:
    let
      hashes = listToAttrs (
        map (
          item:
          let
            parts = match "([^-]+)-(.*)" item;
          in
          {
            name = head parts;
            value = last parts;
          }
        ) (filter (item: item != "") (splitString " " integrity))
      );
      algorithm = findFirst (
        algorithm: hashes ? ${algorithm}
      ) (throw "no hash that Nix reads in ${integrity}") algorithms;
    in
    "${algorithm}-${hashes.${algorithm}}";

  fits =
    wanted: actual:
    if wanted == null || wanted == [ ] then
      true
    else if elem "!${actual}" wanted then
      false
    else
      let
        chosen = filter (item: !hasPrefix "!" item) wanted;
      in
      chosen == [ ] || elem actual chosen;

  cpus = {
    x86_64 = "x64";
    aarch64 = "arm64";
    i686 = "ia32";
    armv7l = "arm";
    riscv64 = "riscv64";
  };
  oses = {
    linux = "linux";
    darwin = "darwin";
    freebsd = "freebsd";
  };
  systemParts = match "([^-]+)-(.*)" system;
  arch = head systemParts;
  kernel = last systemParts;

  on_platform =
    path:
    let
      entry = entryOf path;
    in
    fits (entry.cpu or null) (cpus.${arch} or arch)
    && fits (entry.os or null) (oses.${kernel} or kernel)
    && (kernel != "linux" || fits (entry.libc or null) menu.libc);

  present = path: on_platform path || !((entryOf path).optional or false);

  # Node's walk: `node_modules/<name>` in each directory from `path` up.
  resolve =
    path: name:
    let
      parts = if path == "" then [ ] else splitString "/" path;
      depths = lib.reverseList (range 0 (length parts));
      candidates = concatMap (
        depth:
        if depth > 0 && builtins.elemAt parts (depth - 1) == "node_modules" then
          [ ]
        else
          let
            directory = concatStringsSep "/" (take depth parts);
          in
          [ (if directory == "" then "node_modules/${name}" else "${directory}/node_modules/${name}") ]
      ) depths;
      found = findFirst (candidate: packages ? ${candidate}) null candidates;
    in
    if found == null then
      null
    else if (entryOf found).link or false then
      (entryOf found).resolved
    else if present found then
      found
    else
      null;

  dependencies =
    path:
    let
      entry = entryOf path;
      optional =
        attrNames (entry.optionalDependencies or { })
        ++ attrNames (filterAttrs (_: meta: meta.optional or false) (entry.peerDependenciesMeta or { }));
      names = unique (
        attrNames (entry.dependencies or { })
        ++ attrNames (entry.optionalDependencies or { })
        ++ attrNames (entry.peerDependencies or { })
        ++ lib.optionals (!isNested path) (attrNames (entry.devDependencies or { }))
      );
    in
    concatMap (
      name:
      let
        resolved = resolve path name;
      in
      if resolved != null then
        [ resolved ]
      else if elem name optional then
        [ ]
      else
        throw "pydd.node.npm: the lock gives ${if path == "" then "the project" else path} no ${name}."
    ) names;

  closure =
    path:
    sort (a: b: a < b) (
      filter (dep: dep != path) (
        map (item: item.key) (genericClosure {
          startSet = map (key: { inherit key; }) (dependencies path);
          operator = item: map (key: { inherit key; }) (dependencies item.key);
        })
      )
    );

  nested =
    path:
    let
      prefix = if path == "" then "node_modules/" else "${path}/node_modules/";
    in
    sort (a: b: a < b) (
      filter (
        other:
        hasPrefix prefix other && !((entryOf other).inBundle or false) && !((entryOf other).link or false)
      ) (attrNames packages)
    );

  is_package =
    path:
    let
      entry = entryOf path;
    in
    isNested path && !(entry.link or false) && !(entry.inBundle or false);

  bins_of =
    path: entry:
    let
      bin = entry.bin or null;
    in
    if builtins.isString bin then
      { ${last (splitString "/" (name_of path entry))} = bin; }
    else if builtins.isAttrs bin then
      bin
    else
      { };

  # `posixpath.normpath` of a relative or absolute path.
  normpath =
    path:
    let
      absolute = hasPrefix "/" path;
      parts = filter (part: part != "" && part != ".") (splitString "/" path);
      folded = lib.foldl' (
        kept: part:
        if part == ".." && kept != [ ] && last kept != ".." then
          lib.init kept
        else if part == ".." && absolute then
          kept
        else
          kept ++ [ part ]
      ) [ ] parts;
      joined = concatStringsSep "/" folded;
    in
    if absolute then
      "/" + joined
    else if joined == "" then
      "."
    else
      joined;

  # `posixpath.relpath` between two relative paths.
  relpath =
    target: start:
    let
      to = filter (part: part != ".") (splitString "/" (normpath target));
      from = filter (part: part != ".") (splitString "/" (normpath start));
      common =
        let
          go =
            n:
            if n < length to && n < length from && builtins.elemAt to n == builtins.elemAt from n then
              go (n + 1)
            else
              n;
        in
        go 0;
      parts = lib.genList (_: "..") (length from - common) ++ lib.drop common to;
    in
    if parts == [ ] then "." else concatStringsSep "/" parts;

  bin_links =
    paths:
    listToAttrs (
      concatMap (
        path:
        let
          raw = entryOf path;
          linked = raw.link or false;
          target_path = if linked then raw.resolved else path;
          entry = if linked then entryOf raw.resolved else raw;
          before = lib.init (filter builtins.isString (split "node_modules/" path));
          modules = concatStringsSep "node_modules/" before + "node_modules";
        in
        lib.mapAttrsToList (program: file: {
          name = "${modules}/.bin/${last (splitString "/" program)}";
          value = relpath (normpath "${target_path}/${file}") "${modules}/.bin";
        }) (bins_of path entry)
      ) (sort (a: b: a < b) paths)
    );

  # `pydd.core.tool_env`.
  tool_env =
    tools: path:
    if tools == [ ] then
      { PATH = path; }
    else
      let
        includes = concatStringsSep " " (map (tool: "-isystem ${tool}/include") tools);
      in
      {
        PATH = concatStringsSep ":" (map (tool: "${tool}/bin") tools ++ [ path ]);
        PKG_CONFIG_PATH = concatStringsSep ":" (
          concatMap (tool: [
            "${tool}/lib/pkgconfig"
            "${tool}/share/pkgconfig"
          ]) tools
        );
        CFLAGS = includes;
        CXXFLAGS = includes;
        LIBRARY_PATH = concatStringsSep ":" (map (tool: "${tool}/lib") tools);
      };

  gitPattern = "git\\+(ssh|https?)://(git@)?([^/:]+)[/:](.+)#([0-9a-f]+)";
  gitParts =
    resolved:
    let
      parts = match gitPattern resolved;
    in
    if parts == null then
      null
    else
      {
        host = builtins.elemAt parts 2;
        project = lib.removeSuffix ".git" (builtins.elemAt parts 3);
        commit = builtins.elemAt parts 4;
      };

  hosted_archive =
    resolved:
    let
      git = gitParts resolved;
    in
    if git == null then
      null
    else if git.host == "github.com" then
      "https://codeload.github.com/${git.project}/tar.gz/${git.commit}"
    else if git.host == "gitlab.com" then
      "https://gitlab.com/${git.project}/repository/archive.tar.gz?ref=${git.commit}"
    else if git.host == "git.sr.ht" then
      "https://git.sr.ht/${git.project}/archive/${git.commit}.tar.gz"
    else
      null;

  fetch_name =
    url:
    let
      git = gitParts url;
    in
    if git != null then
      "${store_name (last (splitString "/" git.project))}-${substring 0 7 git.commit}-source"
    else
      store_name (last (splitString "/" (head (splitString "?" url))));

  fixed =
    name: spec: hash: mode:
    derivation {
      inherit name system;
      builder = "${menu.python}/bin/python3";
      args = [
        "-I"
        menu.script
      ];
      PYDD_NPM = toJSON spec;
      SSL_CERT_FILE = "${menu.cacert}/etc/ssl/certs/ca-bundle.crt";
      outputHash = hash;
      outputHashMode = mode;
    };

  tarball =
    path:
    let
      entry = entryOf path;
      replaced = (menu.native.${name_of path entry} or { }).src or null;
      inherit (entry) resolved;
      version = entry.version or "0";
      key = "${name_of path entry}-${version}";
    in
    if replaced != null then
      {
        file = replaced;
        srcs = [ replaced ];
      }
    else if match "https?://.*" resolved != null then
      {
        file = "${fixed (fetch_name resolved) {
          action = "fetch";
          url = resolved;
        } (strongest entry.integrity) "flat"}";
        srcs = [ ];
      }
    else if hasPrefix "git+" resolved || hasPrefix "git:" resolved then
      let
        url = hosted_archive resolved;
      in
      {
        file = "${fixed (fetch_name resolved) {
          action = "archive";
          inherit url;
        } menu.hashes.${key} "recursive"}";
        srcs = [ ];
      }
    else
      {
        file = "${copy (normpath "${directory}/${removePrefix "file:" resolved}")}";
        srcs = [ ];
      };

  # `Graph.copy`: a part of the project, as a node of its own.
  copy =
    path:
    derivation {
      name = store_name (builtins.unsafeDiscardStringContext (last (splitString "/" path)));
      inherit system;
      builder = "${menu.python}/bin/python3";
      args = [
        "-I"
        menu.script
      ];
      PATH = "${menu.nodejs}/bin";
      PYDD_NPM = toJSON {
        action = "copy";
        inherit path;
        srcs = [ menu.project ];
      };
      __contentAddressed = true;
      outputHashMode = "recursive";
      outputHashAlgo = "sha256";
    };

  directory = if menu.subdir == "." then menu.project else normpath "${menu.project}/${menu.subdir}";

  node =
    path:
    let
      entry = entryOf path;
      name = name_of path entry;
      version = entry.version or "0";
      scripted = entry.hasInstallScript or false || builtins.elem name menu.install_scripts;
      source = tarball path;
      deps = if scripted then closure path else [ ];
      tree = listToAttrs (
        map (dep: {
          name = dep;
          value = "${nodes.${dep}}";
        }) deps
      );
      native = if scripted then menu.native.${name} or { } else { };
      tools = (native.tools or [ ]) ++ lib.optionals scripted menu.tools;
      spec = {
        action = "package";
        tarball = source.file;
        inherit (source) srcs;
        inherit path name version;
        bins = bins_of path entry;
        scripts = scripted;
        git =
          (hasPrefix "git+" (entry.resolved or "") || hasPrefix "git:" (entry.resolved or ""))
          && source.srcs == [ ];
        inherit tree;
        links = if scripted then bin_links (deps ++ [ path ]) else { };
        nested = filter (other: tree ? ${other}) (nested path);
        inherit (menu) nodejs;
      };
    in
    derivation (
      tool_env tools "${menu.nodejs}/bin"
      // {
        PYDD_NPM = toJSON spec;
      }
      // (native.env or { })
      // {
        name = "${store_name name}-${version}";
        inherit system;
        builder = "${menu.python}/bin/python3";
        args = [
          "-I"
          menu.script
        ];
        __contentAddressed = true;
        outputHashMode = "recursive";
        outputHashAlgo = "sha256";
      }
    );

  # Every package that the planner's root names: on this platform, fetched.
  paths = filter (path: path != "" && present path && is_package path) (attrNames packages);
  nodes = lib.genAttrs paths node;
in
nodes
