/* eslint-disable @typescript-eslint/no-require-imports -- Compile actual TSX with its local imports for Node's component tests. */
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const root = path.join(__dirname, '../..');
function load(relative) {
  const filename = path.join(root, relative);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX},
  }).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = module.paths;
  const original = mod.require.bind(mod);
  mod.require = id => {
    const local = id.startsWith('@/') ? path.join(root, 'src', id.slice(2)) : id.startsWith('.') ? path.resolve(path.dirname(filename), id) : null;
    if (local) for (const suffix of ['.ts', '.tsx', '']) {
      if (fs.existsSync(local + suffix)) return load(path.relative(root, local + suffix));
    }
    return original(id);
  };
  mod._compile(code, filename);
  return mod.exports;
}
module.exports = {load};
