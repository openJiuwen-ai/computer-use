// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreGraphics
import Foundation

@main enum NativeWindowCapturePixelsTests {
    @MainActor static func main() throws {
        let rgba = CGBitmapInfo.byteOrder32Big.union(CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue))
        let bgra = CGBitmapInfo.byteOrder32Little.union(CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedFirst.rawValue))
        let rgbx = CGBitmapInfo.byteOrder32Big.union(CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue))

        for format in [rgba, bgra] {
            var bytes = [UInt8](repeating: 0, count: 32)
            // Padding cannot count as a visible pixel.
            for offset in [12, 13, 14, 15, 28, 29, 30, 31] { bytes[offset] = 0xff }
            do {
                try WindowCapture.requireVisiblePixels(image(bytes, info: format))
                preconditionFailure("all-zero window alpha was accepted")
            } catch let error as DriverError {
                precondition(error.code == .observationIncomplete)
                precondition(!WindowCaptureRetry.permits(error, originalWindowKnown: true))
            }

            bytes[27] = 1 // The last pixel has the smallest representable positive opacity.
            try WindowCapture.requireVisiblePixels(image(bytes, info: format))
            bytes[27] = 0xff // Opaque black remains valid even with no nonzero color channels.
            try WindowCapture.requireVisiblePixels(image(bytes, info: format))
        }

        try WindowCapture.requireVisiblePixels(image([UInt8](repeating: 0, count: 32), info: rgbx))
        print("PASS transparent native window pixels rejected; padded, partially transparent and opaque images preserved")
    }

    static func image(_ bytes: [UInt8], info: CGBitmapInfo) -> CGImage {
        let provider = CGDataProvider(data: Data(bytes) as CFData)!
        return CGImage(
            width: 3, height: 2, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: 16,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: info, provider: provider, decode: nil,
            shouldInterpolate: false, intent: .defaultIntent
        )!
    }
}
