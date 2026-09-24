// Preload: expone al renderer, de forma segura (contextIsolation), lo mínimo:
//  - la URL del control del mundo
//  - un puente para lanzar/parar el ENTRENAMIENTO en este PC y recibir su log en vivo.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ECONOSIM_CONTROL_ENV", process.env.ECONOSIM_CONTROL || "");

contextBridge.exposeInMainWorld("econosimTrain", {
  start: (opts) => ipcRenderer.invoke("train:start", opts || {}),
  stop: () => ipcRenderer.invoke("train:stop"),
  status: () => ipcRenderer.invoke("train:status"),
  onLog: (cb) => { ipcRenderer.on("train:log", (_e, line) => cb(line)); },
  onDone: (cb) => { ipcRenderer.on("train:done", (_e, info) => cb(info)); },
});

contextBridge.exposeInMainWorld("econosimHistory", {
  list: () => ipcRenderer.invoke("history:list"),
});

contextBridge.exposeInMainWorld("econosimMaquina", {
  ls: (ruta) => ipcRenderer.invoke("agent:ls", ruta),
  read: (ruta) => ipcRenderer.invoke("agent:read", ruta),
});

contextBridge.exposeInMainWorld("econosimFreellm", {
  ask: (prompt) => ipcRenderer.invoke("freellm:ask", prompt),
});
