import AppKit
import ApplicationServices
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

// Passive evidence, intentionally including the decoration unlike model pixels.
@main struct Recorder {
    static func main() async throws {
        let directory = URL(fileURLWithPath: CommandLine.arguments[1])
        let seconds = Double(CommandLine.arguments[2]) ?? 20
        let fixture =
            try JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent("fixture.json")))
            as! [String: Any]
        let pid = (fixture["pid"] as! NSNumber).int32Value
        let frame = fixture["frame"] as! [Double]
        let region = CGRect(x: frame[0], y: frame[1], width: frame[2], height: frame[3])
        let output = directory.appendingPathComponent("frames")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        var index = 0
        let end = Date().addingTimeInterval(seconds)
        while Date() < end,
            !FileManager.default.fileExists(atPath: directory.appendingPathComponent("stop-recorder").path)
        {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first(where: { $0.frame.contains(region) }) else {
                throw NSError(domain: "Missing display", code: 1)
            }
            let windows = content.windows.filter {
                $0.owningApplication?.processID == pid || $0.title == "Computer Use Agent Pointer · v1"
            }
            let filter = SCContentFilter(display: display, including: windows)
            let config = SCStreamConfiguration()
            config.sourceRect = region.offsetBy(dx: -display.frame.minX, dy: -display.frame.minY)
            config.width = Int(region.width)
            config.height = Int(region.height)
            config.showsCursor = false
            let image: CGImage
            do {
                image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
            } catch {
                // Another screenshot can briefly make the evidence recorder's
                // stream unavailable. Keep the gap explicit; the test still
                // requires actual pixels throughout each measured interval.
                let message = "capture gap at \(Date().timeIntervalSince1970): \(error)\n"
                FileHandle.standardError.write(Data(message.utf8))
                try await Task.sleep(for: .milliseconds(100))
                continue
            }
            let url = output.appendingPathComponent(String(format: "%04d.png", index))
            let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
            CGImageDestinationAddImage(destination, image, nil)
            CGImageDestinationFinalize(destination)
            // Detect blue chroma below the title bar: the fixture's neutral
            // controls and green canvas have none. Retain intensity, not just
            // pixel count, to distinguish standby from normal opacity.
            let context = CGContext(
                data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: image.width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
            var count = 0, strength = 0, energy = 0, minX = image.width, minY = image.height, maxX = 0, maxY = 0
            for y in 60..<image.height {
                for x in 0..<image.width {
                    let offset = (y * image.width + x) * 4
                    let red = Int(pixels[offset]), green = Int(pixels[offset + 1]), blue = Int(pixels[offset + 2])
                    if blue - red > 9, blue - green > 7 {
                        strength = max(strength, blue - red)
                        energy += blue - red
                        count += 1
                        minX = min(minX, x)
                        maxX = max(maxX, x)
                        minY = min(minY, y)
                        maxY = max(maxY, y)
                    }
                }
            }
            let catalogue =
                (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                    as? [[String: Any]]) ?? []
            let targetID = (fixture["window"] as! NSNumber).uint32Value
            let covers = catalogue.prefix { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value != targetID }
                .compactMap { item -> [String: Any]? in
                    guard item[kCGWindowName as String] as? String != "Computer Use Agent Pointer · v1",
                        let raw = item[kCGWindowBounds as String] as? [String: Any],
                        let rect = CGRect(dictionaryRepresentation: raw as CFDictionary), rect.intersects(region),
                        (item[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1 > 0
                    else { return nil }
                    return [
                        "owner": item[kCGWindowOwnerName as String] as? String ?? "?",
                        "rect": [rect.minX - region.minX, rect.minY - region.minY, rect.width, rect.height]
                    ]
                }
            let item: [String: Any] = [
                "frame": index, "time": Date().timeIntervalSince1970, "covers": covers, "pointerPixels": count,
                "pointerStrength": strength, "pointerEnergy": energy,
                "pointerBounds": count > 0 ? [minX, minY, maxX, maxY] : [],
                "hitPIDs": [CGPoint(x: 165, y: 111), CGPoint(x: 475, y: 111), CGPoint(x: 350, y: 240)].map {
                    point -> pid_t in
                    var element: AXUIElement?, owner: pid_t = 0
                    if AXUIElementCopyElementAtPosition(
                        AXUIElementCreateSystemWide(), Float(region.minX + point.x), Float(region.minY + point.y),
                        &element) == .success, let element
                    {
                        AXUIElementGetPid(element, &owner)
                    }
                    return owner
                },
                "overlays": windows.filter { $0.title == "Computer Use Agent Pointer · v1" }.map {
                    ["id": $0.windowID, "pid": $0.owningApplication?.processID ?? 0]
                }
            ]
            print(String(data: try JSONSerialization.data(withJSONObject: item), encoding: .utf8)!)
            fflush(stdout)
            index += 1
            try await Task.sleep(for: .milliseconds(35))
        }
    }
}
