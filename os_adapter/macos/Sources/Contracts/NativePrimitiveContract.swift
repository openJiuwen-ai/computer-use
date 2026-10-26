import AppKit
import ApplicationServices

struct NativePrimitiveContract {
    let id: String
    let node: String?
    let within: [String]
    let actuator: NativeInspectQuery.Actuator?
    let receiver: NativeInspectQuery.Receiver?
    let basis: [NativeInspectQuery.Dependency]
    let unit: NativeInputUnit
    var click: NativeClickContract? = nil
}
