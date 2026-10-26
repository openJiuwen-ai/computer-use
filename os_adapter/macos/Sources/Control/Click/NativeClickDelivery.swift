import Foundation

/// The transport of one selected click offer. It never changes during an
/// attempt; the public Gate alone may select another offer before dispatch.
enum NativeClickDelivery: String {
    case objectAction = "object_action"
    case objectSelection = "object_selection"
    case windowDirected = "surface_pointer"
    case systemPointer = "desktop_pointer"
}
