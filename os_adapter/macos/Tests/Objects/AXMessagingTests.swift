import AppKit
import ApplicationServices

/// Run against AXTimeoutProbe: a retained object with a short discovery timeout
/// must receive a fresh mutation timeout, for both actions and attribute writes.
@main struct AXMessagingTests {
    static func main() {
        let app = AXUIElementCreateApplication(pid_t(CommandLine.arguments[1])!)
        func read(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
            AXMessaging.configure(element)
            var value: CFTypeRef?
            return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
        }
        var queue = read(app, "AXWindows") as? [AXUIElement] ?? []
        var found = [String: AXUIElement](), seen = [AXUIElement](), index = 0
        while index < queue.count, index < 200 {
            let element = queue[index]
            index += 1
            if seen.contains(where: { CFEqual($0, element) }) { continue }
            seen.append(element)
            if let id = read(element, "AXIdentifier") as? String { found[id] = element }
            queue += read(element, "AXChildren") as? [AXUIElement] ?? []
        }
        let button = found["short-probe-press"]!, field = found["slow-field"]!
        for (element, action) in [(button, true), (field, false)] {
            precondition(AXUIElementSetMessagingTimeout(element, 0.05) == .success)
            let began = ProcessInfo.processInfo.systemUptime
            let result =
                action
                ? AXMessaging.performAction(element, "AXPress" as CFString)
                : AXMessaging.setAttributeValue(element, "AXValue" as CFString, "direct replacement" as CFString)
            precondition(result == .success, "slow callback failed: \(result.rawValue)")
            precondition(ProcessInfo.processInfo.systemUptime - began >= 0.4)
        }
        precondition(read(button, "AXValue") as? String == "1")
        precondition(read(field, "AXValue") as? String == "direct replacement")
        print("PASS: retained 50 ms proxies allow 450 ms actions and value writes exactly once")
    }
}
