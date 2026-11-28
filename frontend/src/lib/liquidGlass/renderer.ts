// WebGL2 renderer for QuartzCore Liquid Glass panels and Gaussian backdrop mips.

import { configureLinearTexture, createGlResources, disposeGlResources, type GlResources } from "./glResources";
import { MAX_LIQUID_PANELS } from "./shaders";
import type { LiquidPanelSnapshot } from "./surfaceCollector";

export interface RenderOptions {
  dpr: number;
  darkMode: boolean;
  quality: number;
  keyFillAngle: number;
  mapBgRgb: [number, number, number];
}

interface PanelCapture {
  baseTex: WebGLTexture | null;
  overlayTex: WebGLTexture | null;
  baseW: number;
  baseH: number;
  overlayW: number;
  overlayH: number;
  rect: { left: number; bottom: number; width: number; height: number };
}

export interface PixelRect { x: number; y: number; w: number; h: number; }

export function computeOverlayDirtyBounds(
  panels: readonly LiquidPanelSnapshot[],
  width: number,
  height: number,
  dpr: number,
): PixelRect | null {
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const p of panels) {
    const shOff = p.elevation === 0 ? 10 : p.elevation === 2 ? 18 : 14;
    const shRad = p.elevation === 0 ? 24 : p.elevation === 2 ? 36 : 30;
    const pad = (shRad * 2.5 + shOff + 4) * dpr;
    minX = Math.min(minX, p.cx - p.halfW - pad); maxX = Math.max(maxX, p.cx + p.halfW + pad);
    minY = Math.min(minY, height - (p.cy + p.halfH + pad)); maxY = Math.max(maxY, height - (p.cy - p.halfH - pad));
  }
  if (!Number.isFinite(minX)) return null;
  const x = Math.max(0, Math.floor(minX)); const y = Math.max(0, Math.floor(minY));
  const w = Math.min(width, Math.ceil(maxX)) - x; const h = Math.min(height, Math.ceil(maxY)) - y;
  if (w <= 0 || h <= 0) return null;
  return { x, y, w, h };
}

function fingerprintPanels(panels: readonly LiquidPanelSnapshot[]): string {
  let key = "";
  for (const p of panels) {
    key += `${p.cx.toFixed(2)},${p.cy.toFixed(2)},${p.halfW.toFixed(2)},${p.halfH.toFixed(2)},${p.radius.toFixed(2)},${p.opacity.toFixed(4)},${String(p.elevation)};`;
  }
  return key;
}

export class LiquidGlassRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly gl: WebGL2RenderingContext;
  private res: GlResources;
  private readonly panels = new Map<number, PanelCapture>();
  private readonly rectBuf = new Float32Array(MAX_LIQUID_PANELS * 4);
  private readonly metaBuf = new Float32Array(MAX_LIQUID_PANELS * 4);
  private width = 0; private height = 0;
  private backdropW = 0; private backdropH = 0;
  private backdropDirty = true; private backdropVersion = 0; private hasBackdrop = false; private lost = false;
  private lastBgKey = ""; private lastBaseKey = ""; private lastOverlayKey = "";
  private overlayCanvasRef: HTMLCanvasElement | null = null;
  private overlayCtx2d: CanvasRenderingContext2D | null = null;
  private overlayDirtyRect: PixelRect | null = null;

  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false,
    });
    if (gl === null) throw new Error("WebGL2 context unavailable");
    this.canvas = canvas;
    this.gl = gl;
    this.res = createGlResources(gl);
    canvas.addEventListener("webglcontextlost", this.handleContextLost);
    canvas.addEventListener("webglcontextrestored", this.handleContextRestored);
  }

  private readonly handleContextLost = (event: Event): void => { event.preventDefault(); this.lost = true; };

  private readonly handleContextRestored = (): void => {
    this.lost = false; this.panels.clear(); this.hasBackdrop = false;
    this.backdropW = 0; this.backdropH = 0;
    this.lastBaseKey = ""; this.lastOverlayKey = "";
    this.res = createGlResources(this.gl); this.markBackdropDirty();
  };

  isContextLost(): boolean { return this.lost || this.gl.isContextLost(); }

  resize(width: number, height: number): void {
    const nextW = Math.max(1, Math.round(width));
    const nextH = Math.max(1, Math.round(height));
    if (this.width === nextW && this.height === nextH) return;
    this.width = nextW; this.height = nextH; this.canvas.width = nextW; this.canvas.height = nextH;
    if (!this.isContextLost()) { this.backdropW = 0; this.backdropH = 0; this.markBackdropDirty(); }
  }

  private markBackdropDirty(): void { this.backdropDirty = true; this.backdropVersion += 1; }

  retainPanels(activePanels: ReadonlySet<number>): void {
    let removed = false;
    for (const [panel, capture] of this.panels) {
      if (!activePanels.has(panel)) {
        if (capture.baseTex !== null) this.gl.deleteTexture(capture.baseTex); if (capture.overlayTex !== null) this.gl.deleteTexture(capture.overlayTex);
        this.panels.delete(panel);
        removed = true;
      }
    }
    if (this.panels.size === 0) this.hasBackdrop = false;
    if (removed) this.markBackdropDirty();
  }

  capturePanel(panel: number, kind: "base" | "overlay", sourceCanvas: HTMLCanvasElement, rect: DOMRect, singleInterleaved: boolean): boolean {
    if (this.isContextLost() || sourceCanvas.width < 1 || sourceCanvas.height < 1) return false;
    const gl = this.gl;
    let entry = this.panels.get(panel);
    if (entry === undefined) {
      entry = { baseTex: null, overlayTex: null, baseW: 0, baseH: 0, overlayW: 0, overlayH: 0, rect: { left: 0, bottom: 0, width: 0, height: 0 } };
      this.panels.set(panel, entry);
    }
    entry.rect = { left: rect.left, bottom: rect.bottom, width: rect.width, height: rect.height };
    if (singleInterleaved && entry.overlayTex !== null) {
      gl.deleteTexture(entry.overlayTex); entry.overlayTex = null; entry.overlayW = 0; entry.overlayH = 0;
    }
    const isBase = kind === "base"; const existing = isBase ? entry.baseTex : entry.overlayTex; const tex = existing ?? gl.createTexture();
    if (existing === null) configureLinearTexture(gl, tex, false);
    gl.bindTexture(gl.TEXTURE_2D, tex); gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    if (existing !== null && (isBase ? entry.baseW : entry.overlayW) === sourceCanvas.width && (isBase ? entry.baseH : entry.overlayH) === sourceCanvas.height) {
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, sourceCanvas);
    } else {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, sourceCanvas);
    }
    if (isBase) { entry.baseTex = tex; entry.baseW = sourceCanvas.width; entry.baseH = sourceCanvas.height; }
    else { entry.overlayTex = tex; entry.overlayW = sourceCanvas.width; entry.overlayH = sourceCanvas.height; }
    this.hasBackdrop = true; this.markBackdropDirty();
    return true;
  }

  renderFrame(
    basePanels: readonly LiquidPanelSnapshot[],
    overlayPanels: readonly LiquidPanelSnapshot[],
    overlayCanvas2d: HTMLCanvasElement | null,
    options: RenderOptions,
  ): boolean {
    if (this.isContextLost() || !this.hasBackdrop || this.panels.size === 0 || this.width < 1 || this.height < 1) return false;
    const bgKey = `${String(options.mapBgRgb[0])},${String(options.mapBgRgb[1])},${String(options.mapBgRgb[2])},${String(options.dpr)},${String(options.quality)}`;
    if (bgKey !== this.lastBgKey) { this.lastBgKey = bgKey; this.markBackdropDirty(); }
    if ((basePanels.length > 0 || overlayPanels.length > 0) && this.backdropDirty) { this.rebuildBackdrop(options.dpr, options.mapBgRgb); this.backdropDirty = false; }
    const gl = this.gl; gl.bindVertexArray(this.res.vao);
    const optionsKey = `${String(options.dpr)},${String(options.darkMode ? 1 : 0)},${String(options.quality)},${String(options.keyFillAngle)}`;
    const framePrefix = `${String(this.width)}x${String(this.height)}#${String(this.backdropVersion)}#${optionsKey}`;
    let overlayDrew = false;
    const ctx2d = this.overlay2dContext(overlayCanvas2d);
    if (ctx2d !== null) {
      const overlayKey = `${framePrefix}|${fingerprintPanels(overlayPanels)}`;
      if (overlayPanels.length > 0 && overlayKey !== this.lastOverlayKey) {
        const prev = this.overlayDirtyRect;
        if (prev !== null) ctx2d.clearRect(prev.x, prev.y, prev.w, prev.h);
        const dirty = computeOverlayDirtyBounds(overlayPanels, this.width, this.height, options.dpr);
        if (dirty !== null) {
          gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.width, this.height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
          this.drawPanelLayer(overlayPanels, options);
          ctx2d.drawImage(this.canvas, dirty.x, dirty.y, dirty.w, dirty.h, dirty.x, dirty.y, dirty.w, dirty.h);
          overlayDrew = true;
        }
        this.overlayDirtyRect = dirty;
        this.lastOverlayKey = overlayKey;
      } else if (overlayPanels.length === 0 && overlayKey !== this.lastOverlayKey) {
        const prev = this.overlayDirtyRect;
        if (prev !== null) { ctx2d.clearRect(prev.x, prev.y, prev.w, prev.h); this.overlayDirtyRect = null; }
        this.lastOverlayKey = overlayKey;
      }
    }
    const baseKey = `${framePrefix}|${fingerprintPanels(basePanels)}`;
    if (overlayDrew || baseKey !== this.lastBaseKey) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.width, this.height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      if (basePanels.length > 0) this.drawPanelLayer(basePanels, options);
      this.lastBaseKey = baseKey;
    }
    return true;
  }

  dispose(): void {
    this.canvas.removeEventListener("webglcontextlost", this.handleContextLost);
    this.canvas.removeEventListener("webglcontextrestored", this.handleContextRestored);
    for (const capture of this.panels.values()) {
      if (capture.baseTex !== null) this.gl.deleteTexture(capture.baseTex); if (capture.overlayTex !== null) this.gl.deleteTexture(capture.overlayTex);
    }
    this.panels.clear();
    disposeGlResources(this.gl, this.res);
  }

  private overlay2dContext(overlayCanvas2d: HTMLCanvasElement | null): CanvasRenderingContext2D | null {
    if (overlayCanvas2d === null) return null;
    if (overlayCanvas2d.width !== this.width || overlayCanvas2d.height !== this.height) {
      overlayCanvas2d.width = this.width; overlayCanvas2d.height = this.height; this.overlayDirtyRect = null;
    }
    if (this.overlayCanvasRef !== overlayCanvas2d) {
      this.overlayCanvasRef = overlayCanvas2d; this.overlayCtx2d = overlayCanvas2d.getContext("2d"); this.overlayDirtyRect = null; this.lastOverlayKey = "";
    }
    return this.overlayCtx2d;
  }

  private allocateBackdrop(w: number, h: number): void {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.res.backdropTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  }

  private rebuildBackdrop(dpr: number, bgRgb: [number, number, number]): void {
    const backdropScale = dpr > 1 ? 1 / dpr : 1;
    const nextW = Math.max(1, Math.round(this.width * backdropScale));
    const nextH = Math.max(1, Math.round(this.height * backdropScale));
    if (nextW !== this.backdropW || nextH !== this.backdropH) {
      this.backdropW = nextW; this.backdropH = nextH;
      this.allocateBackdrop(nextW, nextH);
    }
    const gl = this.gl;
    const backdropDpr = dpr * (this.backdropW / Math.max(1, this.width));
    gl.bindVertexArray(this.res.vao); gl.bindFramebuffer(gl.FRAMEBUFFER, this.res.backdropFbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.res.backdropTex, 0);
    gl.viewport(0, 0, this.backdropW, this.backdropH);
    let single: PanelCapture | undefined;
    if (this.panels.size === 1) {
      for (const capture of this.panels.values()) single = capture;
    }
    const fullCover = single !== undefined && single.baseTex !== null && single.rect.left * backdropDpr <= 0
      && (single.rect.left + single.rect.width) * backdropDpr >= this.backdropW
      && (single.rect.bottom - single.rect.height) * backdropDpr <= 0
      && single.rect.bottom * backdropDpr >= this.backdropH;
    if (!fullCover) { gl.clearColor(bgRgb[0] / 255, bgRgb[1] / 255, bgRgb[2] / 255, 1); gl.clear(gl.COLOR_BUFFER_BIT); }
    gl.useProgram(this.res.blitProg); gl.activeTexture(gl.TEXTURE0); gl.uniform1i(this.res.blitSrc, 0);
    for (const capture of this.panels.values()) {
      gl.viewport(Math.round(capture.rect.left * backdropDpr), Math.round(this.backdropH - capture.rect.bottom * backdropDpr), Math.max(1, Math.round(capture.rect.width * backdropDpr)), Math.max(1, Math.round(capture.rect.height * backdropDpr)));
      if (capture.baseTex !== null) { gl.disable(gl.BLEND); gl.bindTexture(gl.TEXTURE_2D, capture.baseTex); gl.drawArrays(gl.TRIANGLES, 0, 3); }
      if (capture.overlayTex !== null) { gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.bindTexture(gl.TEXTURE_2D, capture.overlayTex); gl.drawArrays(gl.TRIANGLES, 0, 3); }
    }
    gl.disable(gl.BLEND); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, null, 0); gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const maxLevels = Math.min(3, Math.floor(Math.log2(Math.max(this.backdropW, this.backdropH))) + 1);
    gl.bindTexture(gl.TEXTURE_2D, this.res.backdropTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_BASE_LEVEL, 0);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, maxLevels - 1);
    gl.generateMipmap(gl.TEXTURE_2D);
  }

  private drawPanelLayer(panels: readonly LiquidPanelSnapshot[], options: RenderOptions): void {
    const gl = this.gl;
    const count = Math.min(panels.length, MAX_LIQUID_PANELS);
    if (count === 0) return;
    for (let i = 0; i < count; i += 1) {
      const p = panels[i];
      if (p === undefined) continue;
      const base = i * 4;
      this.rectBuf[base] = p.cx; this.rectBuf[base + 1] = p.cy; this.rectBuf[base + 2] = p.halfW; this.rectBuf[base + 3] = p.halfH;
      this.metaBuf[base] = p.radius; this.metaBuf[base + 1] = p.opacity; this.metaBuf[base + 2] = p.elevation; this.metaBuf[base + 3] = 0;
    }
    gl.useProgram(this.res.glassProg); const u = this.res.glassU;
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.res.backdropTex);
    gl.uniform1i(u.uBackdrop ?? null, 0); gl.uniform2f(u.uResolution ?? null, this.width, this.height);
    gl.uniform1f(u.uDpr ?? null, options.dpr);
    gl.uniform1f(u.uBackdropScale ?? null, this.backdropW / Math.max(1, this.width));
    gl.uniform1f(u.uDarkMode ?? null, options.darkMode ? 1 : 0);
    gl.uniform1f(u.uQuality ?? null, options.quality); gl.uniform2f(u.uKeyFillDir ?? null, Math.cos(options.keyFillAngle), Math.sin(options.keyFillAngle));
    gl.uniform2f(u.uAberrationDir ?? null, 0.9659, 0.2588); gl.uniform1i(u.uPanelCount ?? null, count);
    gl.uniform4fv(u.uPanelRect ?? null, this.rectBuf); gl.uniform4fv(u.uPanelMeta ?? null, this.metaBuf);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
    gl.disable(gl.BLEND);
  }
}
