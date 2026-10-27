import AppKit
import ApplicationServices
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.finishLaunching()
let panel = NSPanel(
    contentRect: NSRect(x: 200, y: 200, width: 240, height: 100), styleMask: [.titled, .utilityWindow],
    backing: .buffered, defer: false)
panel.title = "Owned catalogue probe"
panel.orderBack(nil)
let id = CGWindowID(panel.windowNumber)
panel.orderOut(nil)
let direct = CGWindowListCopyWindowInfo(.optionIncludingWindow, id) as? [[String: Any]] ?? []
let all = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? []
let matching = all.filter { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == id }
let result: [String: Any] = ["pid": getpid(), "window": id, "direct": direct, "catalogue": matching]
let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
assert(direct.isEmpty)
assert(matching.count == 1)
assert((matching[0][kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == getpid())
panel.close()
