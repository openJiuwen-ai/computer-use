import AppKit
import ApplicationServices
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

// Test-only inspection of this project's preview; no foreground activation/input.
@main struct PreviewInspector {
    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        let args = Array(CommandLine.arguments.dropFirst())
        let identifier =
            ProcessInfo.processInfo.environment["COMPUTER_USE_PREVIEW_BUNDLE"] ?? "local.computer-use.driver"
        let drivers = NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
        func emit(_ value: Any) throws {
            print(String(data: try JSONSerialization.data(withJSONObject: value), encoding: .utf8)!)
        }
        if args.first == "list" {
            try emit(drivers.filter { !$0.isTerminated }.map(\.processIdentifier))
            return
        }
        guard args.count >= 2, let pid = Int32(args[1]), let app = drivers.first(where: { $0.processIdentifier == pid })
        else {
            try emit(["windows": 0, "values": [String: String]()])
            return
        }
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.2)
        func attribute(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
            var result: CFTypeRef?
            return AXUIElementCopyAttributeValue(node, key as CFString, &result) == .success ? result : nil
        }
        var values = [String: String](), close: AXUIElement?, visited = [AXUIElement]()
        func visit(_ node: AXUIElement, depth: Int) {
            guard depth < 20, visited.count < 200, !visited.contains(where: { CFEqual($0, node) }) else { return }
            visited.append(node)
            if let id = attribute(node, kAXIdentifierAttribute) as? String,
                let value = attribute(node, kAXValueAttribute) as? String
            {
                values[id] = value
            }
            if attribute(node, kAXRoleAttribute) as? String == kAXButtonRole,
                attribute(node, kAXDescriptionAttribute) as? String == "关闭预览"
            {
                close = node
            }
            for child in attribute(node, kAXChildrenAttribute) as? [AXUIElement] ?? [] {
                visit(child, depth: depth + 1)
            }
        }
        visit(root, depth: 0)
        let windows = (CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? [])
            .filter {
                ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid
                    && $0[kCGWindowName as String] as? String == "Computer Use Preview"
            }
        if args.first == "dismiss" {
            guard let close, AXUIElementPerformAction(close, kAXPressAction as CFString) == .success else {
                throw NSError(
                    domain: "PreviewInspector", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "preview close button unavailable"])
            }
        } else if args.count == 3 {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard
                let window = content.windows.first(where: {
                    $0.owningApplication?.processID == pid && $0.title == "Computer Use Preview"
                })
            else {
                throw NSError(
                    domain: "PreviewInspector", code: 2, userInfo: [NSLocalizedDescriptionKey: "preview not visible"])
            }
            let config = SCStreamConfiguration()
            config.width = Int(window.frame.width)
            config.height = Int(window.frame.height)
            config.showsCursor = false
            config.ignoreShadowsSingleWindow = true
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config)
            let data = NSMutableData()
            let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)!
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else { throw NSError(domain: "PreviewInspector", code: 3) }
            try (data as Data).write(to: URL(fileURLWithPath: args[2]))
        }
        try emit(["windows": windows.count, "values": values, "active": app.isActive])
    }
}
