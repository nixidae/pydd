const ms = require("ms");

module.exports = (name) => `hello from ${name} hour=${ms("1h")}`;
