import {useSyncExternalStore} from 'react';
import {flushSync} from 'react-dom';

export function createStore<T>(initial:T) {
  let snapshot = initial;
  const listeners = new Set<()=>void>();
  return {
    get:()=>snapshot,
    subscribe(listener:()=>void) {listeners.add(listener);return()=>{listeners.delete(listener);};},
    set(next:T) {snapshot=next;flushSync(()=>{for(const listener of listeners)listener();});},
  };
}
export function useStore<T>(store:ReturnType<typeof createStore<T>>) {return useSyncExternalStore(store.subscribe,store.get);}
export function saved(key:string,fallback:string) {try{return localStorage.getItem(key)||fallback;}catch{return fallback;}}
export function save(key:string,value:string|number) {try{localStorage.setItem(key,String(value));}catch{/* Storage can be unavailable. */}}
export function host(id:string) {const element=document.getElementById(id);if(!element)throw new Error('Missing reader surface '+id);return element;}
export function errorMessage(error:unknown) {return error instanceof Error?error.message:String(error);}
