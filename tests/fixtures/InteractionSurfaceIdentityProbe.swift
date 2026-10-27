import AppKit
import ApplicationServices

/// Read-only AX object evidence for a disposable native fixture. Compile with
/// the driver's sources (excluding main.swift), then pass its PID and an
/// observed field identifier. This does not activate, focus, or send input.
@main struct InteractionSurfaceIdentityProbe {
    @MainActor static func main() throws {
        guard CommandLine.arguments.count == 3, let pid = pid_t(CommandLine.arguments[1]), pid > 0 else {
            throw NSError(
                domain: "InteractionSurfaceIdentityProbe", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "expected PID and observed field identifier"])
        }
        let identifier = CommandLine.arguments[2]
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.1)
        var objects = [AXUIElement]()
        func ref(_ element: AXUIElement?) -> Any {
            guard let element else { return NSNull() }
            if let index = objects.firstIndex(where: { CFEqual($0, element) }) { return index }
            objects.append(element)
            return objects.count - 1
        }
        func metadata(_ element: AXUIElement) -> [String: Any] {
            var owner: pid_t = 0
            let status = AXUIElementGetPid(element, &owner)
            return [
                "object": ref(element), "pidStatus": status.rawValue,
                "pid": status == .success ? owner as Any : NSNull(),
                "providerPid": BackgroundEvents.accessibilityProcessID(element) as Any? ?? NSNull(),
                "role": AXAccess.read(element, kAXRoleAttribute).value as? String ?? "unknown",
                "parent": ref(AXAccess.element(element, kAXParentAttribute)),
                "window": ref(AXAccess.element(element, kAXWindowAttribute)),
                "topLevel": ref(AXAccess.element(element, kAXTopLevelUIElementAttribute)),
                "children": (AXAccess.elements(element, kAXChildrenAttribute, limit: 64) ?? []).map(ref)
            ]
        }
        var roots = [(String, AXUIElement)]()
        for (index, window) in (AXAccess.elements(app, kAXWindowsAttribute, limit: 64) ?? []).enumerated() {
            roots.append(("AXWindows[\(index)]", window))
        }
        for key in [kAXFocusedWindowAttribute, kAXMainWindowAttribute] {
            if let window = AXAccess.element(app, key) { roots.append((key, window)) }
        }
        var fields = [[String: Any]](), surfaces = [[String: Any]](), visitedCount = 0
        let deadline = ProcessInfo.processInfo.systemUptime + 15
        let surfaceRoles = [kAXWindowRole, kAXSheetRole, "AXPopover"]
        for (route, root) in roots {
            var pending = [(element: root, path: [AXUIElement](), route: route)], seen = [AXUIElement](), cursor = 0
            while cursor < pending.count, seen.count < 2048, ProcessInfo.processInfo.systemUptime < deadline {
                let item = pending[cursor]
                cursor += 1
                guard item.path.count < 32, !seen.contains(where: { CFEqual($0, item.element) }) else { continue }
                seen.append(item.element)
                visitedCount += 1
                AXUIElementSetMessagingTimeout(item.element, 0.1)
                let role = AXAccess.read(item.element, kAXRoleAttribute).value as? String ?? "unknown"
                if surfaceRoles.contains(role) {
                    var value = metadata(item.element)
                    value["route"] = item.route
                    value["presented"] = ref(WindowGeometry.presentedWindow(item.element, pid: pid))
                    surfaces.append(value)
                }
                if AXAccess.read(item.element, kAXIdentifierAttribute).value as? String == identifier {
                    var value = metadata(item.element)
                    value["route"] = item.route
                    value["ancestors"] = item.path.map(ref)
                    value["owningWindow"] = ref(WindowGeometry.owningWindow(item.element, pid: pid))
                    let observedSurface = item.path.reversed().first {
                        surfaceRoles.contains(AXAccess.read($0, kAXRoleAttribute).value as? String ?? "")
                    }
                    value["observedSurface"] = ref(observedSurface)
                    value["presentedObservedSurface"] = ref(
                        observedSurface.map { WindowGeometry.presentedWindow($0, pid: pid) })
                    fields.append(value)
                }
                for (index, child) in (AXAccess.elements(item.element, kAXChildrenAttribute, limit: 128) ?? [])
                    .enumerated()
                { pending.append((child, item.path + [item.element], item.route + "/AXChildren[\(index)]")) }
            }
        }
        let value: [String: Any] = [
            "pid": pid, "queryOnly": true,
            "sameObjectMeaning": "object numbers merge only CFEqual AX objects within this process",
            "roots": roots.map { ["route": $0.0, "object": ref($0.1)] }, "surfaces": surfaces, "fields": fields,
            "visitedCount": visitedCount, "deadlineReached": ProcessInfo.processInfo.systemUptime >= deadline
        ]
        print(
            String(
                data: try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted]),
                encoding: .utf8)!)
    }
}
