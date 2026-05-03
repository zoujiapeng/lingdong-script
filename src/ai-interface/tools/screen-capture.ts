/**
 * screen_capture Tool — 截图选区工具
 *
 * 调用后弹出一个全屏选区窗口，让你拖拽选择屏幕上的任意区域。
 * 返回选中区域的坐标 {x, y, w, h} 和截图数据。
 * 用于配置 color_check、progress_wait、region_wait 等节点的监控区域。
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';
import { createTool } from './registry';

function getElectronPath(): string {
  // 尝试多个可能的 electron 路径
  const candidates = [
    path.join(process.cwd(), 'node_modules', 'electron', 'cli.js'),
    path.join(__dirname, '..', '..', '..', '..', 'node_modules', 'electron', 'cli.js'),
    path.join(__dirname, '..', '..', '..', 'node_modules', 'electron', 'cli.js'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  // fallback — 假设在 PATH 中
  return 'electron';
}

function getMainJSPath(): string {
  // 寻找 dist/main/main.js (已编译的 Electron 主进程)
  const candidates = [
    path.join(process.cwd(), 'dist', 'main', 'main.js'),
    path.join(__dirname, '..', '..', '..', 'dist', 'main', 'main.js'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return '';
}

async function spawnSelectionOverlay(): Promise<{ x: number; y: number; w: number; h: number; dataURL: string } | null> {
  const electronPath = getElectronPath();
  const mainJSPath = getMainJSPath();

  if (!mainJSPath) {
    // 如果没有编译好的 main.js，尝试用 ts-node/tsx 直接跑
    throw new Error('找不到 dist/main/main.js，请先运行 npm run build:electron');
  }

  // 创建临时文件用于 IPC 通信
  const tmpFile = path.join(require('os').tmpdir(), `ss-result-${Date.now()}.json`);

  // 创建临时 Electron 入口脚本
  const scriptContent = `
const { app, BrowserWindow, desktopCapturer, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');

const RESULT_FILE = ${JSON.stringify(tmpFile)};

app.whenReady().then(async () => {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: sw, height: sh } = primaryDisplay.size;

  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: { width: sw, height: sh },
  });
  if (sources.length === 0) { app.quit(); return; }

  const fullDataURL = sources[0].thumbnail.toDataURL();

  // Create selection overlay window
  const selWin = new BrowserWindow({
    width: sw, height: sh,
    fullscreen: true, frame: false, alwaysOnTop: true,
    resizable: false, show: false,
    webPreferences: { contextIsolation: false, nodeIntegration: true },
  });

  const selHTML = \`<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;cursor:crosshair;user-select:none}
#canvas{display:block;width:100%;height:100%}
#bar{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,0.75);color:#fff;padding:8px 18px;border-radius:8px;
  font:14px system-ui;display:flex;align-items:center;gap:14px;pointer-events:none;z-index:10}
#bar kbd{background:rgba(255,255,255,0.15);padding:2px 7px;border-radius:4px;font:11px system-ui}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="bar"><span>拖拽选择截图区域</span><kbd>Enter</kbd> 确认 <kbd>Esc</kbd> 取消</div>
<script>
const {ipcRenderer}=require("electron");
const c=document.getElementById("c"),ctx=c.getContext("2d");
const img=new Image();
img.onload=()=>{c.width=img.width;c.height=img.height;ctx.drawImage(img,0,0);init()};
img.src='\${fullDataURL}';
let sx,sy,ex,ey,drg=false;
function init(){
  c.onmousedown=e=>{sx=e.offsetX;sy=e.offsetY;drg=true};
  c.onmousemove=e=>{if(!drg)return;ex=e.offsetX;ey=e.offsetY;draw()};
  c.onmouseup=()=>{drg=false};
  c.ondblclick=confirm;
  onkeydown=e=>{if(e.key==="Enter")confirm();if(e.key==="Escape")ipcRenderer.send("ss:cancel")};
}
function draw(){
  ctx.drawImage(img,0,0);
  const x=Math.min(sx,ex),y=Math.min(sy,ey),w=Math.abs(ex-sx),h=Math.abs(ey-sy);
  ctx.fillStyle="rgba(0,0,0,0.5)";ctx.fillRect(0,0,c.width,y);
  ctx.fillRect(0,y,x,h);ctx.fillRect(x+w,y,c.width-x-w,h);ctx.fillRect(0,y+h,c.width,c.height-y-h);
  ctx.strokeStyle="#3b82f6";ctx.lineWidth=3;ctx.setLineDash([6,4]);
  ctx.strokeRect(x,y,w,h);ctx.setLineDash([]);
  ctx.fillStyle="rgba(59,130,246,0.9)";ctx.fillRect(x,y-24,Math.min(w+10,100),20);
  ctx.fillStyle="#fff";ctx.font="12px system-ui";ctx.textAlign="center";
  ctx.fillText(w+"\\u00d7"+h,x+Math.min(w,95)/2,y-8);
}
function confirm(){
  if(sx===void 0||ex===void 0)return;
  const x=Math.round(Math.min(sx,ex)),y=Math.round(Math.min(sy,ey)),w=Math.round(Math.abs(ex-sx)),h=Math.round(Math.abs(ey-sy));
  if(w<5||h<5)return;
  ipcRenderer.send("ss:done",{x,y,w,h});
}
<\/script>
</body>
</html>\`;

  selWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(selHTML));
  selWin.once('ready-to-show', () => {
    // Force fullscreen after show
    selWin.setFullScreen(true);
    selWin.show();
    selWin.focus();
  });

  // Wait for selection
  const result = await new Promise((resolve) => {
    ipcMain.once('ss:done', (_, rect) => {
      const { x, y, w, h } = rect;
      const cropWin = new BrowserWindow({
        show: false,
        webPreferences: { contextIsolation: false, nodeIntegration: true },
      });
      const cropHTML = \`<script>const{ipcRenderer}=require("electron");const i=new Image();i.onload=()=>{const c=document.createElement("canvas");c.width=\${w};c.height=\${h};c.getContext("2d").drawImage(i,\${x},\${y},\${w},\${h},0,0,\${w},\${h});ipcRenderer.send("ss:cropped",c.toDataURL("image/png"))};i.src='\${fullDataURL}';<\/script>\`;
      cropWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(cropHTML));
      ipcMain.once('ss:cropped', (_2, dataURL) => {
        cropWin.close();
        resolve({ x, y, w, h, dataURL });
      });
    });
    ipcMain.once('ss:cancel', () => { resolve(null); });
  });

  selWin.close();
  // Write result to temp file
  fs.writeFileSync(RESULT_FILE, JSON.stringify(result));
  app.quit();
});
`;

  const tmpScript = path.join(require('os').tmpdir(), `ss-app-${Date.now()}.js`);
  fs.writeFileSync(tmpScript, scriptContent, 'utf-8');

  return new Promise((resolve, reject) => {
    const proc = cp.spawn('node', [electronPath, tmpScript], {
      stdio: ['ignore', 'ignore', 'pipe'],
      detached: false,
      windowsHide: true,
    });

    // Timeout after 60 seconds
    const timeout = setTimeout(() => {
      try { proc.kill(); } catch {}
      try { fs.unlinkSync(tmpScript); } catch {}
      try { fs.unlinkSync(tmpFile); } catch {}
      resolve(null);
    }, 60000);

    proc.on('close', () => {
      clearTimeout(timeout);
      try { fs.unlinkSync(tmpScript); } catch {}
      try {
        if (fs.existsSync(tmpFile)) {
          const raw = fs.readFileSync(tmpFile, 'utf-8');
          fs.unlinkSync(tmpFile);
          const data = JSON.parse(raw);
          resolve(data);
        } else {
          resolve(null);
        }
      } catch (e) {
        resolve(null);
      }
    });

    proc.on('error', (err) => {
      clearTimeout(timeout);
      try { fs.unlinkSync(tmpScript); } catch {}
      try { fs.unlinkSync(tmpFile); } catch {}
      reject(new Error(`截图选区启动失败: ${err.message}`));
    });
  });
}

export const screenCaptureTool = createTool(
  {
    name: 'screen_capture',
    description: '【截图选区】弹出全屏选区窗口，让你拖拽选择屏幕上的任意区域。返回选中区域的坐标 {x, y, w, h} 和截图数据。用于配置监控节点的检测区域。',
    priority: 'high',
    category: 'capture',
    inputSchema: {
      type: 'object',
      properties: {
        mode: {
          type: 'string',
          description: '操作模式: "select"=弹出选区窗口(默认)，"full"=直接截取全屏',
          enum: ['select', 'full'],
        },
      },
      required: [],
    },
    examples: [
      'screen_capture { mode: "select" }  ← 弹出选区窗口让你选择区域',
      'screen_capture { mode: "full" }    ← 直接截取全屏',
    ],
  },
  async (args) => {
    const mode = args.mode || 'select';

    if (mode === 'full') {
      // 全屏截图：使用 PowerShell
      const tmpImg = path.join(require('os').tmpdir(), `ss-full-${Date.now()}.png`);
      try {
        cp.execSync(
          `powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; $g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.X,$b.Y,0,0,$b.Size); $bmp.Save('${tmpImg}'); $bmp.Dispose(); $g.Dispose()"`,
          { timeout: 10000 }
        );
        const imgBuffer = fs.readFileSync(tmpImg);
        const base64 = imgBuffer.toString('base64');
        try { fs.unlinkSync(tmpImg); } catch {}
        return {
          success: true,
          data: {
            mode: 'full',
            width: require('child_process').execSync('powershell -NoProfile -Command "([System.Windows.Forms.Screen]::PrimaryScreen.Bounds).Width"').toString().trim(),
            height: require('child_process').execSync('powershell -NoProfile -Command "([System.Windows.Forms.Screen]::PrimaryScreen.Bounds).Height"').toString().trim(),
            base64,
            format: 'image/png',
          },
          summary: '全屏截图完成',
        };
      } catch (e: any) {
        return { success: false, error: `全屏截图失败: ${e.message}` };
      }
    }

    // select 模式 — 弹出选区窗口
    try {
      const result = await spawnSelectionOverlay();
      if (!result) {
        return { success: false, error: '用户取消或超时', data: { cancelled: true } };
      }
      return {
        success: true,
        data: {
          mode: 'select',
          x: result.x,
          y: result.y,
          w: result.w,
          h: result.h,
          base64: result.dataURL.split(',')[1], // strip data:image/png;base64, prefix
          format: 'image/png',
        },
        summary: `选区完成: (${result.x}, ${result.y}) ${result.w}x${result.h}`,
      };
    } catch (e: any) {
      return { success: false, error: `截图选区失败: ${e.message}` };
    }
  },
);
