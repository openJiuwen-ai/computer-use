// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// The existing wire method set, independent of a live executor instance.
enum NativeMethods {
    static let values: Set<String> = ["begin", "locatePoint", "inspect", "capture", "prepareObservation", "execute", "finish"]
}
