// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Settled facts from one native collection, before publication and after any
/// deferred native references and field statuses have been resolved.
struct NativeCollectedObservation {
    typealias Record = [String: Any]

    struct Stamp {
        let id: String
        let startedAt: Int64
        let completedAt: Int64
    }

    enum AttributeRead {
        case record(Record)
        case batch(Int)
    }

    enum NativeProperty {
        case raw(Record)
        // The native encoder proves exact ASCII spelling before sharing.
        case catalogue([String])
    }

    enum NodeRead {
        case record(Record)
        // The collector owns the ordered batch arguments. Pool their values
        // at publication without constructing an intermediate raw read record.
        case batch(fields: [String], status: Int32, queried: Bool)
    }

    struct Node {
        let id: String
        let requested: [String]
        var values: Record
        var statuses: [String: Record]
        let actions: Record
        var extra = [NativeProperty]()
        let read: NodeRead
        let nativeReads: [String: AttributeRead]
        var nativeValues = [String: NativeObservationEncoding.CapturedValue]()
    }

    let session: String
    let surface: String
    let stamp: Stamp
    let roots: [String]
    var nodes = [Node]()
    let relations: [Record]
    var requestedRelations: [String]?
    var unread = [String]()
    var stopReasons = [String]()
    var nativeResources = [Record]()
    var changeToken: Any?
}
