import AppKit
import ApplicationServices

// Acceptance-only oracle: exact disposable document titles in an isolated PID.
// --select establishes test preconditions; ordinary reads never change focus.
let pid = Int32(CommandLine.arguments[1])!
let wanted = Array(CommandLine.arguments[2...3])
let root = AXUIElementCreateApplication(pid)

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    _ = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return value
}

func textArea(_ element: AXUIElement, depth: Int) -> AXUIElement? {
    if attribute(element, kAXRoleAttribute) as? String == kAXTextAreaRole { return element }
    guard depth > 0 else { return nil }
    for child in attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] {
        if let found = textArea(child, depth: depth - 1) { return found }
    }
    return nil
}

let windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
var documents = [[String: Any]]()
for title in wanted {
    guard let window = windows.first(where: { attribute($0, kAXTitleAttribute) as? String == title }),
        let editor = textArea(window, depth: 10), let value = attribute(editor, kAXValueAttribute) as? String
    else { fatalError("owned test document missing") }
    if CommandLine.arguments.contains("--select") {
        var all = CFRange(location: 0, length: value.utf16.count)
        let range = AXValueCreate(.cfRange, &all)!
        precondition(AXUIElementSetAttributeValue(editor, kAXSelectedTextRangeAttribute as CFString, range) == .success)
    }
    var one = CFRange(location: 0, length: 1)
    let range = AXValueCreate(.cfRange, &one)!
    var attributes: CFTypeRef?
    let status = AXUIElementCopyParameterizedAttributeValue(
        editor, kAXAttributedStringForRangeParameterizedAttribute as CFString, range, &attributes)
    let description = String(describing: attributes)
    documents.append([
        "title": title, "text": value, "status": status.rawValue, "attributes": description,
        "bold": description.contains("Bold") || description.contains("bold")
    ])
}
var focusedTitle = "none"
if let focused = attribute(root, kAXFocusedWindowAttribute), CFGetTypeID(focused) == AXUIElementGetTypeID() {
    focusedTitle = attribute(unsafeDowncast(focused, to: AXUIElement.self), kAXTitleAttribute) as? String ?? "unknown"
}
let output = try JSONSerialization.data(
    withJSONObject: ["documents": documents, "focusedTitle": focusedTitle], options: [.prettyPrinted])
print(String(data: output, encoding: .utf8)!)
