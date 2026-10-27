import AppKit
import QuartzCore
import ImageIO
import UniformTypeIdentifiers

/// Render the production component offscreen; no desktop windows or input.
@main struct AgentPointerAppearanceTests {
    @MainActor static func main() throws {
        func render(scale: CGFloat, idle: Bool = false) -> CGContext {
            let size = AgentPointerAssets.size
            let context = CGContext(
                data: nil, width: Int(size.width * scale), height: Int(size.height * scale), bitsPerComponent: 8,
                bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            context.scaleBy(x: scale, y: scale)
            context.translateBy(x: AgentPointerAssets.hotspot.x, y: size.height - AgentPointerAssets.hotspot.y)
            let pointer = AgentPointerAppearance()
            pointer.moveTo(.zero, animated: false, scale: scale)
            pointer.setState(idle ? .idle : .active)
            func prepare(_ layer: CALayer) {
                layer.removeAllAnimations()
                layer.displayIfNeeded()
                for child in layer.sublayers ?? [] { prepare(child) }
            }
            prepare(pointer.layer)
            pointer.layer.render(in: context)
            pointer.stop()
            return context
        }
        func metrics(_ context: CGContext) -> (alpha: Int, visible: Int, halo: Int, colors: Set<UInt32>) {
            let data = context.data!.assumingMemoryBound(to: UInt8.self)
            var alpha = 0, visible = 0, halo = 0, colors = Set<UInt32>()
            for y in 0..<context.height {
                for x in 0..<context.width {
                    let offset = y * context.bytesPerRow + x * 4
                    let a = Int(data[offset + 3])
                    alpha += a
                    if a > 3 && a < 80 { halo += 1 }
                    if a > 100 {
                        visible += 1
                        colors.insert(
                            UInt32(data[offset]) << 16 | UInt32(data[offset + 1]) << 8 | UInt32(data[offset + 2]))
                    }
                }
            }
            return (alpha, visible, halo, colors)
        }
        for scale in [CGFloat(1), 2, 6] {
            let active = render(scale: scale), idle = render(scale: scale, idle: true)
            let a = metrics(active), b = metrics(idle)
            precondition(a.visible > Int(150 * scale * scale), "porcelain artwork is visible at each screen scale")
            precondition(a.visible < Int(280 * scale * scale), "the halo remains translucent around the enlarged arrow")
            precondition(a.halo > Int(500 * scale * scale), "a soft circular halo surrounds the arrow")
            let pixels = active.data!.assumingMemoryBound(to: UInt8.self)
            func alpha(_ x: Int, _ y: Int) -> UInt8 { pixels[y * active.bytesPerRow + x * 4 + 3] }
            let row = Int(36 * scale), outer = Int(16 * scale)
            precondition(
                alpha(outer, row) > 0 && alpha(outer, row) < alpha(Int(20 * scale), row)
                    && alpha(Int(20 * scale), row) < alpha(Int(24 * scale), row), "radial halo fades outward")
            let circle = [
                alpha(outer, row), alpha(active.width - 1 - outer, row), alpha(row, outer),
                alpha(row, active.height - 1 - outer)
            ].map(Int.init)
            precondition(circle.max()! - circle.min()! <= 2, "halo is circular in the rendered pixels")
            precondition(
                alpha(0, row) == 0 && alpha(active.width - 1, row) == 0 && alpha(row, 0) == 0
                    && alpha(row, active.height - 1) == 0, "halo meets the canvas transparently")
            precondition(a.colors.count > 40, "gradient/highlight colors survive native drawing")
            let ratio = Double(b.alpha) / Double(a.alpha)
            precondition(ratio > 0.38 && ratio < 0.42, "idle opacity applies to the complete porcelain artwork")
            let file = URL(fileURLWithPath: ".artifacts/native-tests/pointer-porcelain-\(Int(scale))x.png")
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            let destination = CGImageDestinationCreateWithURL(file as CFURL, UTType.png.identifier as CFString, 1, nil)!
            CGImageDestinationAddImage(destination, active.makeImage()!, nil)
            precondition(CGImageDestinationFinalize(destination))
        }
        print("PASS pointer artwork: production pixels at 1x/2x/6x, gradients, transparency and idle opacity")
    }
}
