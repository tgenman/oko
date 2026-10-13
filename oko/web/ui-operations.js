// One instance belongs to one UI owner. Invalidating its context does not undo
// a server mutation: callers reconcile successful writes outside commit().
export function createOperations() {
  let epoch = 0;
  const pending = new Map();
  function capture(controller, key) {
    const context = epoch;
    const operation = {
      get current() {return context === epoch && (!key || pending.get(key) === operation);},
      signal: controller?.signal,
      commit(effect) {if (operation.current) return effect();},
      cancel() {controller?.abort();},
    };
    return operation;
  }
  return {
    capture() {return capture();},
    begin(key, {abortable = false} = {}) {
      pending.get(key)?.cancel();
      const operation = capture(abortable ? new AbortController() : null, key);
      pending.set(key, operation);
      return operation;
    },
    invalidate() {
      epoch++;
      for (const operation of pending.values()) operation.cancel();
      pending.clear();
    },
  };
}
