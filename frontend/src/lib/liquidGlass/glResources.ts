// WebGL2 shader compilation, program linking, and GPU resource allocation for Liquid Glass.

import { BLIT_FS, FULLSCREEN_VS, LIQUID_GLASS_FS, PANEL_QUAD_VS } from "./shaders";

export interface GlResources {
  blitProg: WebGLProgram;
  blitSrc: WebGLUniformLocation | null;
  glassProg: WebGLProgram;
  glassU: Record<string, WebGLUniformLocation | null>;
  vao: WebGLVertexArrayObject;
  backdropTex: WebGLTexture;
  backdropFbo: WebGLFramebuffer;
}

const GLASS_UNIFORM_NAMES = [
  "uBackdrop",
  "uResolution",
  "uDpr",
  "uBackdropScale",
  "uDarkMode",
  "uQuality",
  "uKeyFillDir",
  "uAberrationDir",
  "uPanelCount",
  "uPanelRect",
  "uPanelMeta",
] as const;

function compileShader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (shader === null) {
    throw new Error("Failed to allocate WebGL2 shader");
  }
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS) !== true) {
    const info = gl.getShaderInfoLog(shader) ?? "Unknown shader compile error";
    gl.deleteShader(shader);
    throw new Error(info);
  }
  return shader;
}

function createProgram(gl: WebGL2RenderingContext, vsSrc: string, fsSrc: string): WebGLProgram {
  const vs = compileShader(gl, gl.VERTEX_SHADER, vsSrc);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, fsSrc);
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (gl.getProgramParameter(prog, gl.LINK_STATUS) !== true) {
    const info = gl.getProgramInfoLog(prog) ?? "Unknown program link error";
    gl.deleteProgram(prog);
    throw new Error(info);
  }
  return prog;
}

export function configureLinearTexture(gl: WebGL2RenderingContext, tex: WebGLTexture, mipmap: boolean): void {
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, mipmap ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
}

export function createGlResources(gl: WebGL2RenderingContext): GlResources {
  const blitProg = createProgram(gl, FULLSCREEN_VS, BLIT_FS);
  const glassProg = createProgram(gl, PANEL_QUAD_VS, LIQUID_GLASS_FS);
  const vao = gl.createVertexArray();
  const backdropTex = gl.createTexture();
  configureLinearTexture(gl, backdropTex, true);
  const glassU: Record<string, WebGLUniformLocation | null> = {};
  for (const name of GLASS_UNIFORM_NAMES) {
    glassU[name] = gl.getUniformLocation(glassProg, name);
  }
  return {
    blitProg,
    blitSrc: gl.getUniformLocation(blitProg, "uSrc"),
    glassProg,
    glassU,
    vao,
    backdropTex,
    backdropFbo: gl.createFramebuffer(),
  };
}

export function disposeGlResources(gl: WebGL2RenderingContext, res: GlResources): void {
  gl.deleteProgram(res.blitProg);
  gl.deleteProgram(res.glassProg);
  gl.deleteVertexArray(res.vao);
  gl.deleteTexture(res.backdropTex);
  gl.deleteFramebuffer(res.backdropFbo);
}
