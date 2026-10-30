// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import Foundation
import Darwin

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let preview = PreviewController()

@MainActor func receive(_ data: Data) {
    guard let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let kind = event["kind"] as? String
    else { return }
    switch kind {
    case "reset": preview.reset()
    case "frame": if let name = event["name"] as? String { preview.frame(name: name, png: event["png"] as? String) }
    default: break
    }
}

let incoming = PreviewLatestValue<Data>(receive: receive)

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.async {
            NSApplication.shared.setActivationPolicy(.accessory)
            try? FileHandle.standardOutput.write(contentsOf: Data("ready\n".utf8))
            DispatchQueue.global(qos: .utility).async {
                var pending = Data()
                while true {
                    let data = FileHandle.standardInput.availableData
                    if data.isEmpty {
                        DispatchQueue.main.async { exit(0) }
                        return
                    }
                    pending.append(data)
                    guard pending.count <= 16 * 1024 * 1024 else { exit(1) }
                    while let end = pending.firstIndex(of: 10) {
                        let line = Data(pending[..<end])
                        pending.removeSubrange(...end)
                        incoming.submit(line)
                    }
                }
            }
        }
    }
}

let delegate = Delegate()
app.delegate = delegate
app.run()
