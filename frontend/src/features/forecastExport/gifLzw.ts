const MAX_CODES = 4096;
const HASH_SIZE = 5003;
const CLEAR_CHECK_INTERVAL = 256;
const CLEAR_RATIO_HYSTERESIS = 0.9;

export interface GifFrameWrite {
  left: number;
  top: number;
  width: number;
  height: number;
  delay: number;
  transparent: boolean;
  transparentIndex: number;
  minCodeSize: number;
  indices: Uint8Array;
  unchanged: Uint8Array | null;
}

export class ByteStream {
  private buf: Uint8Array<ArrayBuffer>;
  private length = 0;

  constructor(initialCapacity = 4096) {
    this.buf = new Uint8Array(initialCapacity);
  }

  private ensure(extra: number): void {
    const needed = this.length + extra;
    if (needed <= this.buf.length) return;
    let next = this.buf.length < 1_048_576 ? this.buf.length * 2 : this.buf.length + (this.buf.length >> 1);
    if (next < needed) next = needed;
    const grown = new Uint8Array(next);
    if (this.length > 0) grown.set(this.buf.subarray(0, this.length), 0);
    this.buf = grown;
  }

  writeByte(value: number): void {
    this.ensure(1);
    this.buf[this.length++] = value;
  }

  writeUint16(value: number): void {
    this.ensure(2);
    this.buf[this.length++] = value & 0xff;
    this.buf[this.length++] = (value >>> 8) & 0xff;
  }

  writeAscii(text: string): void {
    this.ensure(text.length);
    for (let i = 0; i < text.length; i++) this.buf[this.length++] = text.charCodeAt(i);
  }

  writeBytesView(source: Uint8Array, start: number, end: number): void {
    const count = end - start;
    this.ensure(count);
    this.buf.set(source.subarray(start, end), this.length);
    this.length += count;
  }

  bytes(): Uint8Array<ArrayBuffer> { return this.buf.slice(0, this.length); }
  reset(): void { this.length = 0; }
}

export function writeGifHeader(
  out: ByteStream,
  width: number,
  height: number,
  palette: readonly (readonly number[])[],
  gctBits: number,
): void {
  out.writeAscii("GIF89a");
  out.writeUint16(width);
  out.writeUint16(height);
  out.writeByte(0x80 | (7 << 4) | (gctBits - 1));
  out.writeByte(0);
  out.writeByte(0);
  const tableSlots = 1 << gctBits;
  for (let i = 0; i < tableSlots; i++) {
    const entry = palette[i];
    out.writeByte(entry?.[0] ?? 0);
    out.writeByte(entry?.[1] ?? 0);
    out.writeByte(entry?.[2] ?? 0);
  }
  out.writeByte(0x21);
  out.writeByte(0xff);
  out.writeByte(11);
  out.writeAscii("NETSCAPE2.0");
  out.writeByte(3);
  out.writeByte(1);
  out.writeUint16(0);
  out.writeByte(0);
}

/** Stateful GIF89a LZW encoder reusing dictionary storage across frames. */
export class GifLzwEncoder {
  private readonly htab = new Int32Array(HASH_SIZE);
  private readonly codetab = new Int16Array(HASH_SIZE);
  private readonly block = new Uint8Array(255);

  writeFrame(out: ByteStream, frame: GifFrameWrite): void {
    const {
      left, top, width, height, delay, transparent, transparentIndex, minCodeSize,
      indices, unchanged,
    } = frame;

    out.writeByte(0x21); out.writeByte(0xf9); out.writeByte(4);
    out.writeByte((1 << 2) | (transparent ? 1 : 0));
    out.writeUint16(Math.max(0, Math.min(0xffff, Math.round(delay / 10))));
    out.writeByte(transparent ? transparentIndex : 0); out.writeByte(0);

    out.writeByte(0x2c);
    out.writeUint16(left); out.writeUint16(top);
    out.writeUint16(width); out.writeUint16(height);
    out.writeByte(0);
    out.writeByte(minCodeSize);

    const clearCode = 1 << minCodeSize;
    const eoiCode = clearCode + 1;
    let nextCode = eoiCode + 1;
    let codeBits = minCodeSize + 1;
    let maxCode = (1 << codeBits) - 1;

    const { htab, codetab, block } = this;
    const hasTrans = transparent && unchanged !== null;
    htab.fill(-1);

    let bitBuf = 0, bitCount = 0, blockLen = 0;
    let full = false, codesSinceCheck = 0, pixelsSinceCheck = 0, bestRatio = 0;

    const flushBlock = (): void => {
      if (blockLen === 0) return;
      out.writeByte(blockLen);
      out.writeBytesView(block, 0, blockLen);
      blockLen = 0;
    };
    const emit = (code: number): void => {
      bitBuf |= code << bitCount;
      bitCount += codeBits;
      while (bitCount >= 8) {
        block[blockLen++] = bitBuf & 0xff;
        bitBuf >>>= 8;
        bitCount -= 8;
        if (blockLen === 255) flushBlock();
      }
      if (nextCode > maxCode && codeBits < 12) { codeBits++; maxCode = (1 << codeBits) - 1; }
    };
    const resetDict = (): void => {
      emit(clearCode);
      htab.fill(-1);
      nextCode = eoiCode + 1;
      codeBits = minCodeSize + 1;
      maxCode = (1 << codeBits) - 1;
      full = false; codesSinceCheck = 0; pixelsSinceCheck = 0; bestRatio = 0;
    };
    const probe = (parent: number, sym: number): number => {
      const key = (sym << 12) | parent;
      let slot = ((sym << 4) ^ parent) % HASH_SIZE;
      if (htab[slot] === key) return codetab[slot] ?? -1;
      if (htab[slot] === -1) return -1;
      const step = slot === 0 ? 1 : HASH_SIZE - slot;
      do {
        slot -= step;
        if (slot < 0) slot += HASH_SIZE;
        if (htab[slot] === key) return codetab[slot] ?? -1;
      } while (htab[slot] !== -1);
      return -1;
    };
    const insert = (parent: number, sym: number): void => {
      const code = nextCode++;
      const key = (sym << 12) | parent;
      let slot = ((sym << 4) ^ parent) % HASH_SIZE;
      if (htab[slot] !== -1) {
        const step = slot === 0 ? 1 : HASH_SIZE - slot;
        do { slot -= step; if (slot < 0) slot += HASH_SIZE; } while (htab[slot] !== -1);
      }
      htab[slot] = key;
      codetab[slot] = code;
    };

    emit(clearCode);
    const len = width * height;
    let pos = 0;
    let prevCode = -1;
    while (pos < len) {
      const startUnch = hasTrans && unchanged[pos] === 1;
      const startActual = indices[pos] ?? 0;
      let cur = startUnch ? transparentIndex : startActual;
      let firstSym = cur;
      let p = pos + 1;

      if (startUnch) {
        let cT = transparentIndex, pT = pos + 1;
        while (pT < len) {
          const u = unchanged[pT] === 1;
          let nxt = u ? probe(cT, transparentIndex) : -1;
          if (nxt === -1) nxt = probe(cT, indices[pT] ?? 0);
          if (nxt === -1) break;
          cT = nxt; pT++;
        }
        if (pT - pos <= 2) {
          let cA = startActual, pA = pos + 1;
          while (pA < len) {
            const u = unchanged[pA] === 1;
            let nxt = probe(cA, indices[pA] ?? 0);
            if (nxt === -1 && u) nxt = probe(cA, transparentIndex);
            if (nxt === -1) break;
            cA = nxt; pA++;
          }
          if (pA > pT) { cur = cA; firstSym = startActual; p = pA; }
          else { cur = cT; p = pT; }
        } else { cur = cT; p = pT; }
      } else {
        while (p < len) {
          const u = hasTrans && unchanged[p] === 1;
          let nxt = u ? probe(cur, transparentIndex) : -1;
          if (nxt === -1) nxt = probe(cur, indices[p] ?? 0);
          if (nxt === -1) break;
          cur = nxt; p++;
        }
      }

      if (prevCode !== -1 && !full) insert(prevCode, firstSym);
      emit(cur);
      const consumed = p - pos;
      pos = p;
      prevCode = cur;
      if (pos < len) {
        if (!full) {
          if (nextCode >= MAX_CODES) { full = true; codesSinceCheck = 0; pixelsSinceCheck = 0; bestRatio = 0; }
        } else if (++codesSinceCheck >= CLEAR_CHECK_INTERVAL) {
          const ratio = (pixelsSinceCheck + consumed) / codesSinceCheck;
          if (ratio > bestRatio) bestRatio = ratio;
          else if (ratio < bestRatio * CLEAR_RATIO_HYSTERESIS) { resetDict(); prevCode = -1; }
          codesSinceCheck = 0; pixelsSinceCheck = 0;
        } else pixelsSinceCheck += consumed;
      }
    }

    emit(eoiCode);
    if (bitCount > 0) {
      block[blockLen++] = bitBuf & 0xff;
      if (blockLen === 255) flushBlock();
    }
    flushBlock();
    out.writeByte(0);
  }
}
