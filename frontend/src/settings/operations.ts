export type Operation = {readonly current:boolean; signal?:AbortSignal; commit<T>(effect:()=>T):T|undefined; cancel():void};
export function createOperations() {
  let epoch=0;
  const pending=new Map<string,Operation>();
  const capture=(controller?:AbortController,key?:string):Operation=>{
    const context=epoch;
    const operation:Operation={get current(){return context===epoch&&(!key||pending.get(key)===operation);},signal:controller?.signal,
      commit(effect){if(operation.current)return effect();},cancel(){controller?.abort();}};
    return operation;
  };
  return {capture:()=>capture(),begin(key:string,{abortable=false}={}){pending.get(key)?.cancel();const action=capture(abortable?new AbortController():undefined,key);pending.set(key,action);return action;},
    invalidate(){epoch++;for(const action of pending.values())action.cancel();pending.clear();}};
}
