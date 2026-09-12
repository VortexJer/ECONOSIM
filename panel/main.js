// Proceso principal de Electron: ventana que carga el panel.
// El panel solo LEE el mundo (y controla el reloj) por el API de control en localhost.
const { app, BrowserWindow } = require("electron");
const path = require("path");

const CONTROL = process.env.ECONOSIM_CONTROL || "http://127.0.0.1:8080";

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    backgroundColor: "#0a0b0d",
    title: "ECONOSIM · Panel",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // inyecta la URL del control antes de cargar
  win.webContents.on("did-finish-load", () => {
    win.webContents.executeJavaScript(`window.ECONOSIM_CONTROL = ${JSON.stringify(CONTROL)};`);
  });
  win.loadFile(path.join(__dirname, "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
