// A pinned umbrella version carries `^` ranges on its sub-packages, so npm can
// install a newer pre-release of every dsh-* package while the umbrella itself
// stays put. A harness assembled from mixed versions loads no plugin tree:
// runs die with "plugin tree failed to load: <plugin> could not be resolved"
// while --dump-config keeps passing, because plugins load per run and not when
// the config is dumped. Fail the image build instead of shipping that.
import fs from 'node:fs';

const root = process.argv[2] ?? '/usr/local/lib/node_modules/@deepseek-ai/dsh';
const wanted = JSON.parse(fs.readFileSync(`${root}/package.json`, 'utf8')).version;
const scope = `${root}/node_modules/@deepseek-ai`;

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
