// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreGraphics
import Foundation
import ImageIO
import Vision

/// OCR evidence from the exact encoded image. Recognition never changes the
/// pixels or their coordinate mapping; an unavailable result is explicit.
enum CaptureOCR {
    private static let maxLines = 64
    private static let maxUnits = 8192
    private static let maxLineUnits = 256
    private static let budget: TimeInterval = 0.8

    private final class OCRValue: @unchecked Sendable {
        let value: [String: Any]
        init(_ value: [String: Any]) { self.value = value }
    }

    private final class Settlement: @unchecked Sendable {
        private let lock = NSLock()
        private var open = true
        func settle() -> Bool {
            lock.lock()
            defer { lock.unlock() }
            guard open else { return false }
            open = false
            return true
        }
    }

    /// Recognize the captured window image without blocking the AppKit owner.
    /// A timed-out request leaves the pixels available and reports why text is
    /// missing instead of discarding the capture.
    @MainActor
    static func read(base64: String, width: Int, height: Int) async -> [String: Any] {
        guard let data = Data(base64Encoded: base64) else {
            return unavailable("captured PNG payload is invalid")
        }
        let value = await withCheckedContinuation { (continuation: CheckedContinuation<OCRValue, Never>) in
            let settlement = Settlement()
            DispatchQueue.global(qos: .userInitiated).async {
                let result = recognize(data, width: width, height: height)
                if settlement.settle() { continuation.resume(returning: OCRValue(result)) }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + budget) {
                if settlement.settle() {
                    continuation.resume(returning: OCRValue(
                        unavailable("recognition exceeded the 800 ms budget; pixels remain available")))
                }
            }
        }
        return value.value
    }

    private static func recognize(_ data: Data, width: Int, height: Int) -> [String: Any] {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { return unavailable("captured PNG could not be decoded for recognition") }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = true }
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        } catch {
            return unavailable(String(describing: error))
        }
        let observations = (request.results ?? []).sorted { left, right in
            if abs(left.boundingBox.maxY - right.boundingBox.maxY) > 0.01 {
                return left.boundingBox.maxY > right.boundingBox.maxY
            }
            return left.boundingBox.minX < right.boundingBox.minX
        }
        var lines: [[String: Any]] = []
        var truncated = false
        var units = 0
        for observation in observations {
            if lines.count == maxLines || units >= maxUnits {
                truncated = true
                break
            }
            guard let candidate = observation.topCandidates(1).first else { continue }
            var text = candidate.string
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            let limit = min(maxLineUnits, maxUnits - units)
            if text.utf16.count > limit {
                text = truncate(text, toUTF16: limit)
                truncated = true
            }
            if text.isEmpty {
                truncated = true
                continue
            }
            let box = observation.boundingBox
            let x = max(0, box.minX * Double(width))
            let y = max(0, (1 - box.maxY) * Double(height))
            let right = min(Double(width), box.maxX * Double(width))
            let bottom = min(Double(height), (1 - box.minY) * Double(height))
            guard x.isFinite, y.isFinite, right.isFinite, bottom.isFinite, right > x, bottom > y else {
                truncated = true
                continue
            }
            lines.append([
                "text": text,
                "rect": ["x": x, "y": y, "width": right - x, "height": bottom - y]
            ])
            units += text.utf16.count
        }
        let language = request.recognitionLanguages.first ?? "auto"
        return [
            "source": "macOSVision", "status": truncated ? "partial" : "complete", "language": language,
            "lines": lines, "truncated": truncated
        ]
    }

    private static func truncate(_ text: String, toUTF16 limit: Int) -> String {
        guard limit > 0 else { return "" }
        var used = 0
        var end = text.startIndex
        for index in text.indices {
            let width = String(text[index]).utf16.count
            if used + width > limit { break }
            used += width
            end = text.index(after: index)
        }
        return String(text[..<end])
    }

    private static func unavailable(_ reason: String) -> [String: Any] {
        let text = reason.isEmpty ? "recognition unavailable" : reason
        let bounded = truncate(text, toUTF16: 512)
        return [
            "source": "macOSVision", "status": "unavailable", "language": NSNull(),
            "lines": [[String: Any]](), "truncated": false, "reason": bounded
        ]
    }
}
