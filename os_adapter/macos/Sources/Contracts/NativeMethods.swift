import Foundation

/// The existing wire method set, independent of a live executor instance.
enum NativeMethods { static let values: Set<String> = ["begin", "inspect", "capture", "execute", "finish"] }
