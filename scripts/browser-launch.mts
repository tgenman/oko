// Browser executable selection shared by tests and one-off prototype snapshots.
import {existsSync} from 'node:fs';
import {chromium,type LaunchOptions} from 'playwright';
export function browserLaunchOptions():LaunchOptions {
 const configured=process.env.OKO_BROWSER_EXECUTABLE;
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 return {headless:true,...(configured?{executablePath:configured}:process.platform==='darwin'&&existsSync(chrome)?{executablePath:chrome}:{})};
}
export function launchBrowser(options:LaunchOptions={}){return chromium.launch({...browserLaunchOptions(),...options});}
