// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Darwin

/// The Launch Services instance receives the SDK's original stdio pipes.
enum AppConnection {
    static func attach(_ path: String) throws {
        let files = try receiveFiles(path)
        defer {
            for file in files.prefix(3) { close(file) }
        }
        for (source, standard) in zip(files, [STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO]) {
            guard dup2(source, standard) >= 0 else { throw DriverError("could not attach driver stdio") }
        }
        // The MCP supervisor waits for this writer to close even if its Python
        // worker dies. Keep it until this process exits, including input cleanup.
        if files.count == 4 { _ = fcntl(files[3], F_SETFD, FD_CLOEXEC) }
    }

    private static func receiveFiles(_ path: String) throws -> [Int32] {
        let socket = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard socket >= 0 else { throw DriverError("could not create driver connection") }
        defer { close(socket) }
        var address = sockaddr_un()
        let name = path.utf8CString
        let capacity = MemoryLayout.size(ofValue: address.sun_path)
        guard name.count <= capacity else { throw DriverError("driver connection path is too long") }
        let addressLength = MemoryLayout<sockaddr_un>.size
        address.sun_len = UInt8(addressLength)
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { bytes in
            name.withUnsafeBytes { bytes.copyMemory(from: $0) }
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(socket, $0, socklen_t(addressLength))
            }
        }
        guard connected == 0 else { throw DriverError("could not connect to the SDK") }

        // This is the layout of a single Darwin SCM_RIGHTS control message.
        let headerSize = MemoryLayout<cmsghdr>.stride
        let descriptorSize = MemoryLayout<Int32>.stride
        var control = [Int32](repeating: 0, count: headerSize / descriptorSize + 4)
        var byte: UInt8 = 0
        var header = msghdr()
        let count = withUnsafeMutableBytes(of: &byte) { payload in
            var vector = iovec(iov_base: payload.baseAddress, iov_len: payload.count)
            return withUnsafeMutablePointer(to: &vector) { buffer in
                control.withUnsafeMutableBytes { rights in
                    header.msg_iov = buffer
                    header.msg_iovlen = 1
                    header.msg_control = rights.baseAddress
                    header.msg_controllen = socklen_t(rights.count)
                    return recvmsg(socket, &header, 0)
                }
            }
        }
        let metadata = control.withUnsafeBytes { $0.load(as: cmsghdr.self) }
        let length = Int(metadata.cmsg_len) - headerSize
        guard count == 1, header.msg_flags & MSG_CTRUNC == 0,
            metadata.cmsg_level == SOL_SOCKET, metadata.cmsg_type == SCM_RIGHTS,
            length == 3 * descriptorSize || length == 4 * descriptorSize,
            metadata.cmsg_len <= header.msg_controllen
        else { throw DriverError("SDK did not supply the driver pipes") }
        let start = headerSize / descriptorSize
        return Array(control[start..<(start + length / descriptorSize)])
    }
}
