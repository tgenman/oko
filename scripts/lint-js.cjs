const {ESLint} = require('eslint');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const root=path.resolve(__dirname,'..');
  const eslint=new ESLint({cwd:root,overrideConfigFile:path.join(root,'eslint.config.cjs')});
  const args=process.argv.slice(2);
  const frontend=path.join(root,'frontend');
  const hasFrontendJs=fs.existsSync(frontend)&&fs.readdirSync(frontend,{recursive:true}).some(name=>/\.(js|mjs)$/.test(name));
  const results=args[0]==='--stdin'
    ? await eslint.lintText(fs.readFileSync(0,'utf8'),{filePath:args[1]})
    : await eslint.lintFiles(args.length?args:[...(hasFrontendJs?['frontend/**/*.{js,mjs}']:[]),'tests/browser/*.cjs','scripts/*.cjs','eslint.config.cjs']);
  process.stdout.write((await eslint.loadFormatter('stylish')).format(results));
  process.exitCode=results.some(result=>result.errorCount||result.warningCount)?1:0;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
