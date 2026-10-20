#!/usr/bin/env node
import { createRequire } from "node:module";
import styles from "ansi-styles";
import semver from "semver";

// chalk 4 is CommonJS and resolves its own ansi-styles 4, which is nested
// under it, while this module gets ansi-styles 6 from the top.
const require = createRequire(import.meta.url);
const chalk = require("chalk");
const nested = require(require.resolve("ansi-styles", { paths: [require.resolve("chalk")] }));

const plain = new chalk.Instance({ level: 0 });
console.log(
  plain.bold("hello from hello-npm"),
  `semver=${semver.major("7.6.3")}`,
  `styles=${typeof styles.modifier === "object" ? 6 : 0}/${typeof nested.modifier === "object" ? 4 : 0}`,
);
