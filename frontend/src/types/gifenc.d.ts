declare module "gifenc" {
  export function quantize(rgba: Uint8Array | Uint8ClampedArray, maxColors: number): number[][];
  export function applyPalette(rgba: Uint8Array | Uint8ClampedArray, palette: number[][]): Uint8Array;
  export function GIFEncoder(options?: { auto?: boolean }): {
    writeHeader(): void;
    writeFrame(indices: Uint8Array, width: number, height: number, options: {
      first?: boolean; palette?: number[][]; delay?: number; repeat?: number; dispose?: number;
    }): void;
    bytes(): Uint8Array<ArrayBuffer>;
    reset(): void;
  };
}
