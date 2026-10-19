// Write derivations from inside a build, as Nix's JSON (nixidae/nixidae#782).
//
//   pydd-drv add [--store URI]              one derivation on stdin; prints its path
//   pydd-drv submit [--store URI] PATH OUT  PATH becomes output OUT of this build
//   pydd-drv graph [--store URI] [--out | --dry-run]
//                                           a whole graph on stdin; see README.md
//
// `add` and `submit` are the bodies of `nix derivation add` and `nix store
// submit-output`, so a planner needs this binary and not the `nix` closure.
// `graph` writes every derivation of a graph in one process, resolves the
// placeholders between them, and submits the root. With `--out` it writes the
// root as ATerm to `$out` instead, which is what released Nix reads.

#include "nix/store/derivations.hh"
#include "nix/store/downstream-placeholder.hh"
#include "nix/store/globals.hh"
#include "nix/store/store-api.hh"
#include "nix/store/store-open.hh"
#include "nix/util/configuration.hh"
#include "nix/util/file-system.hh"
#include "nix/util/terminal.hh"
#if PYDD_HAS_SUBMIT
#  include "nix/store/store-cast.hh"
#  include "nix/store/submit-store.hh"
#endif

#include <nlohmann/json.hpp>

#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

using json = nlohmann::json;
using namespace nix;

namespace {

Derivation parse(Store & store, const json & value)
{
#if PYDD_HAS_SUBMIT
    return derivation::parseJsonAndValidate(store, value);
#else
    return Derivation::parseJsonAndValidate(store, value);
#endif
}

void submit(Store & store, const StorePath & path, [[maybe_unused]] const std::string & output)
{
#if PYDD_HAS_SUBMIT
    require<SubmitStore>(store).submitOutput(SingleDerivedPath::Opaque{path}, output);
#else
    throw Error("this Nix has no builder-rpc-v0, so '%s' cannot be submitted; use 'graph --out'",
                store.printStorePath(path));
#endif
}

// A graph names its own nodes by key. A key of `inputs.drvs` that is a node of
// the graph is symbolic; any other key is the basename of a `.drv` that exists.
// In a string, `{{key^out}}` is the placeholder of output `out` of node `key`,
// and `{{key^drv^out}}` the placeholder of output `out` of the derivation that
// output `drv` of `key` is.
struct Graph
{
    json derivations;
    std::string root;
    std::map<std::string, StorePath> written;

    std::string placeholderOf(const std::string & token) const
    {
        auto parts = tokenizeString<std::vector<std::string>>(token, "^");
        if (parts.size() < 2)
            throw Error("'{{%s}}' names no output: write '{{%s^out}}'", token, token);
        auto node = written.find(parts[0]);
        if (node == written.end())
            throw Error("'{{%s}}' names '%s', which is not a node written before this one", token, parts[0]);
        auto placeholder = DownstreamPlaceholder::unknownCaOutput(node->second, parts[1]);
        for (size_t i = 2; i < parts.size(); ++i)
            placeholder = DownstreamPlaceholder::unknownDerivation(placeholder, parts[i]);
        return placeholder.render();
    }

    std::string substitute(const std::string & text) const
    {
        std::string result;
        size_t at = 0;
        while (true) {
            auto open = text.find("{{", at);
            if (open == std::string::npos)
                break;
            auto close = text.find("}}", open + 2);
            if (close == std::string::npos)
                break;
            result += text.substr(at, open - at);
            result += placeholderOf(text.substr(open + 2, close - open - 2));
            at = close + 2;
        }
        return result + text.substr(at);
    }

    // A planner in any language gets three things for free: a path in
    // `inputs` may be absolute, an input needs no empty `dynamicOutputs`, and
    // a floating output needs no `$out` in `env`, as `builtins.derivation`
    // fills it in.
    json resolve(const std::string & key, const Store & store) const
    {
        json drv = derivations.at(key);
        auto base = [&](const std::string & path) {
            return hasPrefix(path, store.storeDir + "/") ? std::string(baseNameOf(path)) : path;
        };
        for (auto & [name, output] : drv.value("outputs", json::object()).items())
            if (output.contains("hashAlgo") && !output.contains("hash") && !drv["env"].contains(name))
                drv["env"][name] = hashPlaceholder(name);
        if (drv.contains("inputs") && drv["inputs"].contains("srcs"))
            for (auto & src : drv["inputs"]["srcs"])
                src = base(src.get<std::string>());
        for (auto & arg : drv["args"])
            arg = substitute(arg.get<std::string>());
        for (auto & [name, value] : drv["env"].items())
            value = substitute(value.get<std::string>());
        if (drv.contains("inputs") && drv["inputs"].contains("drvs")) {
            json drvs = json::object();
            for (auto & [name, value] : drv["inputs"]["drvs"].items()) {
                auto node = written.find(name);
                auto & entry = drvs[node == written.end() ? base(name) : std::string(node->second.to_string())];
                entry = value;
                if (!entry.contains("dynamicOutputs"))
                    entry["dynamicOutputs"] = json::object();
            }
            drv["inputs"]["drvs"] = drvs;
        }
        return drv;
    }

    std::vector<std::string> order() const
    {
        std::vector<std::string> sorted;
        std::set<std::string> done, visiting;
        std::function<void(const std::string &)> visit = [&](const std::string & key) {
            if (done.count(key))
                return;
            if (!visiting.insert(key).second)
                throw Error("the graph has a cycle through '%s'", key);
            auto & drv = derivations.at(key);
            if (drv.contains("inputs") && drv["inputs"].contains("drvs"))
                for (auto & [name, _] : drv["inputs"]["drvs"].items())
                    if (derivations.contains(name))
                        visit(name);
            visiting.erase(key);
            done.insert(key);
            sorted.push_back(key);
        };
        for (auto & [key, _] : derivations.items())
            visit(key);
        return sorted;
    }
};

// On released Nix the root is the planner's `$out`, and Nix registers no
// reference to a path that the sandbox lacks: the root then silently becomes
// another derivation. So every path it names must be present.
void requirePresent(const Store & store, const json & drv, const std::string & path)
{
    std::vector<std::string> missing;
    auto check = [&](const std::string & base) {
        auto full = store.storeDir + "/" + base;
        if (!pathExists(full))
            missing.push_back(full);
    };
    if (drv.contains("inputs")) {
        for (auto & src : drv["inputs"].value("srcs", json::array()))
            check(src.get<std::string>());
        for (auto & [name, _] : drv["inputs"].value("drvs", json::object()).items())
            check(name);
    }
    if (!missing.empty())
        throw Error(
            "%s names %s, which this build does not have. On released Nix a planner writes one derivation, "
            "so every other one it names must be an input of the planner, made before the build.",
            path,
            concatStringsSep(", ", missing));
}

int graph(const std::optional<std::string> & storeUri, bool toOut, bool dryRun)
{
    auto input = json::parse(std::cin);
    Graph graph{input.at("derivations"), input.at("root").get<std::string>(), {}};
    if (!graph.derivations.contains(graph.root))
        throw Error("the root '%s' is not a node of the graph", graph.root);

    auto store = storeUri ? openStore(*storeUri) : toOut || dryRun ? openStore("dummy://?read-only=false") : openStore();

    json root;
    for (auto & key : graph.order()) {
        auto resolved = graph.resolve(key, *store);
        if (key == graph.root)
            root = resolved;
        graph.written.insert_or_assign(key, store->writeDerivation(parse(*store, resolved)));
    }

    auto rootPath = graph.written.at(graph.root);
    if (dryRun) {
        // Written, and nothing else: the paths are the result.
    } else if (toOut) {
        auto out = getEnv("out");
        if (!out)
            throw Error("--out writes the root to $out, and this build has none");
        requirePresent(*store, root, store->printStorePath(rootPath));
        writeFile(*out, store->requireStoreObjectAccessor(rootPath)->readFile(CanonPath::root));
    } else {
        submit(*store, rootPath, "out");
    }

    json paths = json::object();
    for (auto & [key, path] : graph.written)
        paths[key] = store->printStorePath(path);
    std::cout << paths.dump() << "\n";
    return 0;
}

} // namespace

int main(int argc, char ** argv)
{
    try {
        initLibStore();
        // A floating output and a dynamic placeholder both need these, and the
        // caller's configuration may not set them.
        experimentalFeatureSettings.set("experimental-features", "ca-derivations dynamic-derivations");

        std::vector<std::string> args(argv + 1, argv + argc);
        std::optional<std::string> storeUri;
        bool toOut = false;
        bool dryRun = false;
        std::vector<std::string> positional;
        for (size_t i = 0; i < args.size(); ++i) {
            if (args[i] == "--store" && i + 1 < args.size())
                storeUri = args[++i];
            else if (args[i] == "--out")
                toOut = true;
            else if (args[i] == "--dry-run")
                dryRun = true;
            else
                positional.push_back(args[i]);
        }
        auto open = [&]() { return storeUri ? openStore(*storeUri) : openStore(); };

        if (positional.size() == 1 && positional[0] == "add") {
            auto store = open();
            auto drv = parse(*store, json::parse(std::cin));
            std::cout << store->printStorePath(store->writeDerivation(drv)) << "\n";
            return 0;
        }
        if (positional.size() == 3 && positional[0] == "submit") {
            auto store = open();
            submit(*store, store->parseStorePath(positional[1]), positional[2]);
            return 0;
        }
        if (positional.size() == 1 && positional[0] == "graph")
            return graph(storeUri, toOut, dryRun);

        std::cerr << "usage: pydd-drv add | submit PATH OUTPUT | graph [--out | --dry-run]  [--store URI]\n";
        return 2;
    } catch (BaseError & e) {
        std::cerr << "pydd-drv: " << filterANSIEscapes(e.msg(), true) << "\n";
        return 1;
    } catch (std::exception & e) {
        std::cerr << "pydd-drv: " << e.what() << "\n";
        return 1;
    }
}
