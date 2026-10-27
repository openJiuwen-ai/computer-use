import AppKit

/// A visible grid with repeated/empty values and a spanning cell. AX positions
/// are independent of labels, so the client must preserve the real ranges.
@MainActor final class GridCell: NSView {
    let rowRange: NSRange
    let columnRange: NSRange
    let text: String
    var pressed: (() -> Void)?

    init(row: Int, column: Int, rows: Int = 1, columns: Int = 1, text: String) {
        rowRange = NSRange(location: row, length: rows)
        columnRange = NSRange(location: column, length: columns)
        self.text = text
        super.init(frame: NSRect(x: column * 110, y: (3 - row - rows) * 70, width: columns * 110, height: rows * 70))
        setAccessibilityElement(true)
        setAccessibilityRole(.cell)
        setAccessibilityIdentifier("cell-\(row)-\(column)")
        wantsLayer = true
        layer?.backgroundColor = NSColor.textBackgroundColor.cgColor
        layer?.borderWidth = 1
        layer?.borderColor = NSColor.separatorColor.cgColor
        let label = NSTextField(labelWithString: text)
        label.frame = NSRect(x: 10, y: 10, width: frame.width - 20, height: 22)
        label.setAccessibilityElement(false)
        addSubview(label)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }
    override func accessibilityValue() -> Any? { text }
    override func accessibilityRowIndexRange() -> NSRange { rowRange }
    override func accessibilityColumnIndexRange() -> NSRange { columnRange }
    override func accessibilityChildren() -> [Any]? { [] }
    override func accessibilityPerformPress() -> Bool {
        pressed?()
        return true
    }
}

@MainActor final class Grid: NSView {
    var cells = [GridCell]()
    override func accessibilityChildren() -> [Any]? { CommandLine.arguments.contains("--relations-only") ? [] : cells }
    override func accessibilityVisibleCells() -> [Any]? { cells }
    override func accessibilityRowCount() -> Int { 3 }
    override func accessibilityColumnCount() -> Int { 3 }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
    var window: NSWindow!
    var pressed = [[Int]]()
    func publish() {
        try! JSONSerialization.data(withJSONObject: ["pressed": pressed], options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 400, height: 300), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Native Table Facts Probe"
        let grid = Grid(frame: NSRect(x: 25, y: 30, width: 330, height: 210))
        grid.setAccessibilityElement(true)
        grid.setAccessibilityRole(.table)
        grid.setAccessibilityLabel("Sample grid")
        grid.setAccessibilityIdentifier("sample-grid")
        let entries = [
            (0, 0, 1, 1, "same"), (0, 1, 1, 1, "same"), (0, 2, 1, 1, ""), (1, 0, 1, 1, ""), (2, 0, 1, 1, "tail"),
            (1, 1, 2, 2, "span")
        ]
        for (row, column, rows, columns, text) in entries {
            let cell = GridCell(row: row, column: column, rows: rows, columns: columns, text: text)
            cell.pressed = { [weak self] in
                self?.pressed.append([row, column])
                self?.publish()
            }
            grid.cells.append(cell)
            grid.addSubview(cell)
        }
        window.contentView!.addSubview(grid)
        publish()
        window.orderBack(nil)
    }
}

let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
