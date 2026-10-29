# The Pages workflow, as a value. `.github/workflows/pages.yml` is rendered
# from this file: edit this one, then run `nix run --file . internal.ci-workflow-update`.
# `checks.ciWorkflow` fails when the two differ.
#
# It is the one workflow of pydd's own repository, which josh exports from
# nixidae (nixidae/nixidae#4). Tests run in nixidae.
{
  lib,
  ghalib,
  nixVersion,
}:
let
  mainOnly = "github.ref == 'refs/heads/main'";

  bootstrap = {
    checkout.enable = true;
    nix.install = {
      enable = true;
      version = nixVersion;
      # `flakes` gives `builtins.fetchTree`, which nix/sources.nix fetches
      # nixidae with.
      experimentalFeatures = [
        "nix-command"
        "flakes"
      ];
      settings = {
        trusted-users = [
          "root"
          "runner"
        ];
        substituters = [
          "https://cache.nixos.org?priority=1"
          "https://lillecarl.cachix.org?priority=2"
        ];
        trusted-public-keys = [
          "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
          "lillecarl.cachix.org-1:NN/LLMg7mbyvZCu32Qlo8LpSHqNw7Rr3VBCEYQvRpT0="
        ];
      };
    };
  };
in
ghalib.evalWorkflow {
  name = "Pages";

  on = {
    push.branches = [ "main" ];
    workflow_dispatch = null;
  };

  jobs = {
    # Sphinx renders docs/, and docs/_generated holds the measurements that
    # `nix run --file . internal.measure-modes` wrote. A runner cannot run a
    # `builder-rpc-v0` planner in a private store, so the site never
    # measures.
    docs-build = {
      "if" = mainOnly;
      runs-on = "ubuntu-24.04";
      timeout-minutes = 20;
      ghanix = bootstrap;
      steps = [
        {
          name = "Build documentation";
          run = "nix build --file . docs.default --out-link result --print-build-logs --print-out-paths";
        }
        {
          name = "Prepare Pages artifact";
          run = ''
            mkdir -p public
            cp -r --no-preserve=mode,ownership result/. public/
          '';
        }
        {
          uses = "actions/upload-pages-artifact@v3";
          "with".path = "public";
        }
      ];
    };

    docs-deploy = {
      "if" = mainOnly;
      needs = "docs-build";
      runs-on = "ubuntu-24.04";
      ghanix.freeDiskSpace.enable = false;
      permissions = {
        pages = "write";
        id-token = "write";
      };
      environment = {
        name = "github-pages";
        url = "\${{ steps.deployment.outputs.page_url }}";
      };
      concurrency = {
        group = "pages";
        cancel-in-progress = false;
      };
      steps = [
        {
          name = "Deploy to GitHub Pages";
          id = "deployment";
          uses = "actions/deploy-pages@v4";
        }
      ];
    };
  };
}
