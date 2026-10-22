// Capture an existing local prototype; does not start the Oko runtime or install browsers.
import {parseArgs} from 'node:util';
import path from 'node:path';
import {mkdir} from 'node:fs/promises';
import {launchBrowser} from './browser-launch.mts';
const {values}=parseArgs({options:{url:{type:'string'},output:{type:'string'},width:{type:'string',default:'1440'},height:{type:'string',default:'1000'},ready:{type:'string'}}});
if(!values.url||!values.output)throw new Error('Usage: npm run browser:snapshot -- --url http://127.0.0.1:PORT/ --output /tmp/snapshot.png [--ready SELECTOR] [--width 1440 --height 1000]');
const url=new URL(values.url);
if(!['http:','https:'].includes(url.protocol)||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.username||url.password)throw new Error('Snapshot URL must use loopback HTTP(S)');
const width=Number(values.width),height=Number(values.height);
if(![width,height].every(value=>Number.isInteger(value)&&value>0&&value<=10000))throw new Error('Invalid viewport dimensions');
const browser=await launchBrowser();
try{
 const page=await browser.newPage({viewport:{width,height}});
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url.href,{waitUntil:'networkidle',timeout:15000});
 if(values.ready)await page.locator(values.ready).waitFor({timeout:10000});
 await page.waitForFunction('document.fonts.status === \"loaded\"');
 const output=path.resolve(values.output);await mkdir(path.dirname(output),{recursive:true});
 await page.screenshot({path:output,fullPage:true});
 console.log(JSON.stringify({output,viewport:{width,height},errors}));
 if(errors.length)process.exitCode=1;
}finally{await browser.close();}
