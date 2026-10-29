// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// RESOURCE: retain the existing capture-only observation budget at its original timestamp.
enum CreateCaptureObservationBudgetPass {
    static func run(deadline: TimeInterval, startedAt: TimeInterval) -> ObservationBudget {
        ObservationBudget(deadline: deadline, startedAt: startedAt)
    }
}
