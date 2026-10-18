// THROWAWAY: same reader route, isolated entry point, no production API.
import {defineConfig} from 'vite';
import {resolve} from 'node:path';
export default defineConfig({root:resolve(import.meta.dirname,'../..'),plugins:[{name:'feed-menu-prototype',transformIndexHtml:html=>html.replace('/src/main.tsx','/src/reading/feed-menu.prototype.tsx')}],server:{host:'127.0.0.1',port:5188,strictPort:true}});
