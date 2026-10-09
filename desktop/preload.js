const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("ae", {
  info: () => ipcRenderer.invoke("info"),
  step: (name) => ipcRenderer.invoke("step", name),
  secretsStatus: () => ipcRenderer.invoke("secrets-status"),
  secretSave: (name, value) => ipcRenderer.invoke("secret-save", name, value),
  onLog: (fn) => ipcRenderer.on("log", (_e, m) => fn(m)),
});
