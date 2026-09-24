// A pinned umbrella version carries `^` ranges on its sub-packages, so npm can
// install a newer pre-release of every dsh-* package while the umbrella itself
// stays put. A harness assembled from mixed versions loads no plugin tree:
// runs die with "plugin tree failed to load: <plugin> could not be resolved"
// while --dump-config keeps passing, because plugins load per run and not when
// the config is dumped. Fail the image build instead of shipping that.
import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] ?? '/usr/local/lib/node_modules/@deepseek-ai/dsh';
const wanted = JSON.parse(fs.readFileSync(`${root}/package.json`, 'utf8')).version;

// A global install nests the scope under the umbrella package; a lockfile-based
// project install (`npm ci`) flattens it beside it. Support both.
const scope = [
  `${root}/node_modules/@deepseek-ai`,
  path.dirname(root),
].find((candidate) => fs.existsSync(candidate));
if (!scope) {
  console.error(`no @deepseek-ai scope directory found next to ${root}`);
  process.exit(1);
}

const mismatched = [];
for (const name of fs.readdirSync(scope)) {
  // Only dsh's own packages share one release train; cordis, cosmokit,
  // schemastery and node-addon-system version independently.
  if (!name.startsWith('dsh-')) continue;
  const manifest = `${scope}/${name}/package.json`;
  if (!fs.existsSync(manifest)) continue;
  const { version } = JSON.parse(fs.readFileSync(manifest, 'utf8'));
  if (version !== wanted) mismatched.push(`${name}@${version}`);
}

if (mismatched.length) {
  console.error(`dsh ${wanted} is mixed with: ${mismatched.join(', ')}`);
  process.exit(1);
}
console.log(`dsh tree consistent at ${wanted}`);
