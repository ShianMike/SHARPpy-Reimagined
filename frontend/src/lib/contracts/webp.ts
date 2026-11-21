import { ContractError, expectExactKeys, expectInteger, expectObject, expectString } from "./guards";
export interface WebpFrame {
    width: number;
    height: number;
    bytes: Uint8Array<ArrayBuffer>;
}
export function parseWebpFrame(value: unknown): WebpFrame {
    const object = expectObject(value, "");
    expectExactKeys(object, "", ["width", "height", "dataBase64"]);
    const width = expectInteger(object.width, "width", { min: 1, max: 16383 });
    const height = expectInteger(object.height, "height", { min: 1, max: 16383 });
    const encoded = expectString(object.dataBase64, "dataBase64", { minLength: 20, maxLength: 8 * 1024 * 1024 });
    let binary: string;
    try {
        binary = atob(encoded);
    }
    catch {
        throw new ContractError("dataBase64", "expected base64 WebP");
    }
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < bytes.length; index++) {
        bytes[index] = binary.charCodeAt(index);
    }
    if (binary.slice(0, 4) !== "RIFF" || binary.slice(8, 12) !== "WEBP" || new DataView(bytes.buffer).getUint32(4, true) + 8 !== bytes.length)
        throw new ContractError("dataBase64", "expected a complete WebP frame");
    return { width, height, bytes };
}
