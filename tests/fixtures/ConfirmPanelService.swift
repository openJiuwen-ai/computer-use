import ApplicationServices
import Foundation

// Fixture-only setup: exercise the native file panel's declared confirm action,
// which can leave AX focus on the view-service root rather than its presenter.
let targetPID = pid_t(CommandLine.arguments[1])!
let root = AXUIElementCreateApplication(targetPID)
func value(_ e: AXUIElement, _ key: String) -> CFTypeRef? {
    var raw: CFTypeRef?
    _ = AXUIElementCopyAttributeValue(e, key as CFString, &raw)
    return raw
}
let window = unsafeDowncast(value(root, kAXFocusedWindowAttribute)!, to: AXUIElement.self)
var pending = [window], field: AXUIElement?
var inspected = 0
while !pending.isEmpty, inspected < 200 {
    inspected += 1
    let node = pending.removeFirst()
    if value(node, kAXIdentifierAttribute) as? String == "PathTextField" {
        field = node
        break
    }
    pending += value(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
let status = AXUIElementPerformAction(field!, kAXConfirmAction as CFString)
let focus = unsafeDowncast(value(root, kAXFocusedUIElementAttribute)!, to: AXUIElement.self)
let library = dlopen(
    "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices", RTLD_LAZY)!
typealias ActualPID = @convention(c) (AXUIElement, UnsafeMutablePointer<pid_t>) -> Int32
let actualPID = unsafeBitCast(dlsym(library, "_AXUIElementGetActualPid")!, to: ActualPID.self)
var service: pid_t = 0
_ = actualPID(focus, &service)
print(
    String(
        data: try! JSONSerialization.data(withJSONObject: [
            "status": status.rawValue, "presenter": targetPID, "actual": service,
            "focusRole": value(focus, kAXRoleAttribute) as? String ?? ""
        ]), encoding: .utf8)!)
