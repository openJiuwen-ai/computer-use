// swift-tools-version: 6.0
// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import PackageDescription

let package = Package(
    name: "ComputerUseDriver", platforms: [.macOS(.v14)],
    products: [.executable(name: "computer-use-driver", targets: ["ComputerUseDriver"])],
    targets: [.executableTarget(name: "ComputerUseDriver", path: "Sources")])
