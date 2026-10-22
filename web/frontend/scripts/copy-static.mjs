import { copyFileSync, mkdirSync, readdirSync, rmSync, statSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const target = resolve(root, '../../src/l4d2_cli/web/static');
const expected = resolve(root, '../..');
if (!target.startsWith(expected + '/') && !target.startsWith(expected + '\\')) throw new Error('Invalid output path');
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
function copy(from, to) {
  mkdirSync(to, { recursive: true });
  for (const name of readdirSync(from)) {
    const source = resolve(from, name), destination = resolve(to, name);
    if (statSync(source).isDirectory()) copy(source, destination);
    else copyFileSync(source, destination);
  }
}
copy(resolve(root, 'dist'), target);
console.log(`Built panel assets: ${target}`);
