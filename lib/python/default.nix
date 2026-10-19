{ pkgs, pydd }:
{
  /**
    A Python environment from a uv project, planned at build time.

    Takes the arguments of `./uv.nix`; `project` is the directory that holds
    `pyproject.toml` and `uv.lock`. Returns an ordinary derivation of the
    environment, or of the application, with `override` over these arguments.
    Its `passthru` holds `planner`, `dynamic` (the output that the planner
    submits), `closure`, `env`, `app`, `shell`, `environment` and `lockCheck`.
  */
  uv = pkgs.lib.makeOverridable (args: import ./uv.nix ({ inherit pkgs pydd; } // args));

  /**
    A Python environment from a Poetry project, planned at build time.

    Takes the arguments of `./poetry.nix`; `project` is the directory that
    holds `pyproject.toml` and `poetry.lock`, at lock version 2.1 or later.
    Returns an ordinary derivation, as `uv` does, with `planner`, `dynamic`,
    `closure` and `env` in its `passthru`.
  */
  poetry = pkgs.lib.makeOverridable (args: import ./poetry.nix ({ inherit pkgs pydd; } // args));

  /**
    A Python environment from a PEP 751 lock file, planned at build time.

    Takes the arguments of `./pylock.nix`; `project` is the directory that
    holds `pylock.toml`. Reading the file needs the experimental feature
    `parse-toml-timestamps`. Returns an ordinary derivation, as `uv` does, with
    `planner`, `dynamic`, `closure` and `env` in its `passthru`.
  */
  pylock = pkgs.lib.makeOverridable (args: import ./pylock.nix ({ inherit pkgs pydd; } // args));
}
