import Foundation
import CoreGraphics
import ImageIO

@main struct CapturedImageTests {
    static func main() throws {
        let context = CGContext(
            data: nil, width: 3, height: 2, bitsPerComponent: 8, bytesPerRow: 12, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 1, green: 0, blue: 0, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 3, height: 2))
        let image = context.makeImage()!
        let png = try CapturedImage.encodePNG(image)
        let bytes = Data(base64Encoded: png)!
        precondition(Array(bytes.prefix(8)) == [137, 80, 78, 71, 13, 10, 26, 10])
        let source = CGImageSourceCreateWithData(bytes as CFData, nil)!
        let decoded = CGImageSourceCreateImageAtIndex(source, 0, nil)!
        precondition(decoded.width == 3 && decoded.height == 2)
        let roundTrip = CGContext(
            data: nil, width: 3, height: 2, bitsPerComponent: 8, bytesPerRow: 12, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        roundTrip.draw(decoded, in: CGRect(x: 0, y: 0, width: 3, height: 2))
        precondition(Data(bytes: context.data!, count: 24) == Data(bytes: roundTrip.data!, count: 24))
        let interval = CaptureInterval()
        let state = CapturedImage.WindowState(sampledAt: CaptureClock.now(), onScreen: true, ownerForeground: nil)
        let captured = CapturedImage(png: png, width: 3, height: 2, interval: interval, windowState: state)
        let wire = captured.protocolValue(kind: .window, scope: "window")
        let sampled = wire["windowState"] as! [String: Any]
        precondition(sampled["onScreen"] as? Bool == true && sampled["ownerForeground"] is NSNull)
        precondition(captured.startedAt <= state.sampledAt && state.sampledAt <= captured.completedAt)
        precondition(wire["png"] as? String == png && wire["id"] as? String == captured.id)
        let legacy = CapturedImage(png: png, width: 3, height: 2, interval: interval)
        precondition(legacy.protocolValue(kind: .window, scope: "window")["windowState"] == nil)
        precondition(captured.protocolValue(kind: .desktop, scope: "desktop")["windowState"] == nil)
        RequestDiagnostics.$deadline.withValue(ProcessInfo.processInfo.systemUptime - 1) {
            do {
                _ = try CapturedImage.encodePNG(image)
                preconditionFailure("expired request published pixels")
            } catch { precondition(error is RequestDeadlineExceeded) }
        }
        let budget = ObservationBudget(deadline: 1, startedAt: 0, clock: { 2 })
        RequestDiagnostics.$observationBudget.withValue(budget) {
            do {
                _ = try CapturedImage.encodePNG(image)
                preconditionFailure("expired collection published pixels")
            } catch { precondition(error is ObservationBudgetExceeded) }
        }
        print(
            """
            PASS shared PNG encoding preserves pixels, dimensions, bounded window-state samples and \
            observation deadlines
            """)
    }
}
